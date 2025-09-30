from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Optional, List, Dict, Tuple
from datetime import datetime, timedelta
import re
import os

from binance_client import BinanceClient
from polymarket_parser import PolymarketParser, Market
from strategy_engine import StrategyEngine


app = FastAPI(title="Polymarket Strategy Mirror")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

binance = BinanceClient()
polymarket = PolymarketParser()
engine = StrategyEngine()


def extract_expiry_from_slug(slug: str) -> Optional[datetime]:
    """Extract expiration date from event slug like 'september-29-october-5'"""
    month_map = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4,
        'may': 5, 'june': 6, 'july': 7, 'august': 8,
        'september': 9, 'october': 10, 'november': 11, 'december': 12
    }
    
    # Pattern: "month-day" at the end (e.g., "october-5")
    pattern = r'(\w+)-(\d{1,2})(?:\D|$)'
    matches = re.findall(pattern, slug.lower())
    
    if matches:
        # Take the last date mentioned (usually the end date)
        month_str, day_str = matches[-1]
        month = month_map.get(month_str)
        if month:
            day = int(day_str)
            year = datetime.now().year
            # If month has passed this year, assume next year
            expiry = datetime(year, month, day)
            if expiry < datetime.now():
                expiry = datetime(year + 1, month, day)
            # Add one day since expiry is typically at midnight of next day
            return expiry + timedelta(days=1)
    
    return None


def calculate_delta_neutral_pairs(markets: List[Market], anchor: float) -> Dict[float, Dict]:
    """
    Calculate delta-neutral unit pairs for each strike.
    
    For downside (strike <= anchor): Buy NO at current + Buy YES at strike below
    For upside (strike > anchor): Buy YES at current + Buy NO at strike above
    
    Skip resolved markets (where YES=1.0 or NO=1.0)
    
    Returns: {strike: {'partner_strike': float, 'cost': float, 'pnl': float, 'yes_price': float, 'no_price': float}}
    """
    pairs = {}
    
    # Filter out resolved markets and sort by strike (ascending order)
    open_markets = sorted(
        [m for m in markets if m.strike and m.yes_price < 0.99 and m.no_price < 0.99],
        key=lambda m: m.strike.K
    )
    
    for i, market in enumerate(open_markets):
        strike = market.strike.K
        
        if strike <= anchor:
            # Downside: Buy NO at current + Buy YES at strike BELOW (lower strike)
            if i > 0:
                partner = open_markets[i - 1]  # Lower strike (below)
                # Skip if partner is also resolved
                if partner.yes_price >= 0.99 or partner.no_price >= 0.99:
                    continue
                    
                cost = market.no_price + partner.yes_price
                pnl = 1.0 - cost
                pairs[strike] = {
                    'partner_strike': partner.strike.K,
                    'cost': cost,
                    'pnl': pnl,
                    'no_price': market.no_price,
                    'yes_price': partner.yes_price,
                    'direction': 'downside'
                }
        else:
            # Upside: Buy YES at current + Buy NO at strike ABOVE (higher strike)
            if i < len(open_markets) - 1:
                partner = open_markets[i + 1]  # Higher strike (above)
                # Skip if partner is also resolved
                if partner.yes_price >= 0.99 or partner.no_price >= 0.99:
                    continue
                    
                cost = market.yes_price + partner.no_price
                pnl = 1.0 - cost
                pairs[strike] = {
                    'partner_strike': partner.strike.K,
                    'cost': cost,
                    'pnl': pnl,
                    'yes_price': market.yes_price,
                    'no_price': partner.no_price,
                    'direction': 'upside'
                }
    
    return pairs


def calculate_apy(pnl: float, cost: float, days_to_expiry: int) -> float:
    """Calculate APY from PnL, cost, and days to expiry"""
    if cost <= 0 or days_to_expiry <= 0:
        return 0.0
    
    return_pct = (pnl / cost) * 100
    apy = return_pct * (365 / days_to_expiry)
    return apy


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    budget: float = 1000.0,
    bias: float = 0.0,
    risk_cap: Optional[float] = None,
    asset: str = "ETH"
):
    """Main page - shows Ethereum event with default parameters"""
    slug = "what-price-will-ethereum-hit-september-29-october-5"
    
    # Get anchor price from Binance
    if asset == "ETH":
        anchor = await binance.get_eth_price() or 4000.0
    elif asset == "SOL":
        anchor = await binance.get_sol_price() or 200.0
    else:
        anchor = await binance.get_btc_price() or 95000.0
    
    # Parse Polymarket event
    event = await polymarket.parse_event_by_slug(slug)
    
    if not event or not event.markets:
        return templates.TemplateResponse(
            "error.html",
            {
                "request": request,
                "error": f"Could not load event: {slug}",
                "details": "Event not found or no markets available"
            },
            status_code=404
        )
    
    # Calculate strategy
    orders, summary = engine.calculate_symmetric_strategy(
        markets=event.markets,
        anchor=anchor,
        budget=budget,
        bias=bias,
        risk_cap=risk_cap
    )
    
    # Calculate delta-neutral pairs and APY
    expiry_date = extract_expiry_from_slug(slug)
    days_to_expiry = 7  # Default fallback
    if expiry_date:
        days_to_expiry = max(1, (expiry_date - datetime.now()).days)
    
    pairs = calculate_delta_neutral_pairs(event.markets, anchor)
    
    # Add APY to each pair
    for strike, pair_data in pairs.items():
        pair_data['apy'] = calculate_apy(
            pair_data['pnl'],
            pair_data['cost'],
            days_to_expiry
        )
    
    return templates.TemplateResponse(
        "mirror.html",
        {
            "request": request,
            "event": event,
            "anchor": anchor,
            "asset": asset,
            "budget": budget,
            "bias": bias,
            "orders": orders,
            "summary": summary,
            "pairs": pairs,
            "days_to_expiry": days_to_expiry,
            "expiry_date": expiry_date
        }
    )


@app.get("/demo", response_class=HTMLResponse)
async def demo(
    request: Request,
    budget: float = 1000.0,
    bias: float = 0.0,
    risk_cap: Optional[float] = None,
    asset: str = "BTC"
):
    """
    Demo page with sample BTC ladder event
    """
    from polymarket_parser import Event, Market, StrikeMeta
    
    if asset == "ETH":
        anchor = await binance.get_eth_price() or 4000.0
    elif asset == "SOL":
        anchor = await binance.get_sol_price() or 200.0
    else:
        anchor = await binance.get_btc_price() or 95000.0
    
    demo_markets = [
        Market(
            id="btc_80k",
            question="Will Bitcoin hit $80,000 by Dec 31, 2025?",
            outcome_type="binary",
            strike=StrikeMeta(raw="$80k", K=80000, unit="USD"),
            yes_price=0.35,
            no_price=0.65,
            spread=0.02
        ),
        Market(
            id="btc_90k",
            question="Will Bitcoin hit $90,000 by Dec 31, 2025?",
            outcome_type="binary",
            strike=StrikeMeta(raw="$90k", K=90000, unit="USD"),
            yes_price=0.42,
            no_price=0.58,
            spread=0.02
        ),
        Market(
            id="btc_100k",
            question="Will Bitcoin hit $100,000 by Dec 31, 2025?",
            outcome_type="binary",
            strike=StrikeMeta(raw="$100k", K=100000, unit="USD"),
            yes_price=0.55,
            no_price=0.45,
            spread=0.02
        ),
        Market(
            id="btc_110k",
            question="Will Bitcoin hit $110,000 by Dec 31, 2025?",
            outcome_type="binary",
            strike=StrikeMeta(raw="$110k", K=110000, unit="USD"),
            yes_price=0.38,
            no_price=0.62,
            spread=0.02
        ),
        Market(
            id="btc_120k",
            question="Will Bitcoin hit $120,000 by Dec 31, 2025?",
            outcome_type="binary",
            strike=StrikeMeta(raw="$120k", K=120000, unit="USD"),
            yes_price=0.25,
            no_price=0.75,
            spread=0.02
        ),
    ]
    
    event = Event(
        id="demo_btc_ladder",
        title="Bitcoin Price Ladder - 2025",
        description="Will Bitcoin reach these price levels by December 31, 2025?",
        slug="demo",
        markets=demo_markets
    )
    
    orders, summary = engine.calculate_symmetric_strategy(
        markets=event.markets,
        anchor=anchor,
        budget=budget,
        bias=bias,
        risk_cap=risk_cap
    )
    
    return templates.TemplateResponse(
        "mirror.html",
        {
            "request": request,
            "event": event,
            "anchor": anchor,
            "asset": asset,
            "budget": budget,
            "bias": bias,
            "orders": orders,
            "summary": summary
        }
    )


@app.get("/mirror", response_class=HTMLResponse)
async def mirror(
    request: Request,
    slug: str,
    budget: float = 1000.0,
    bias: float = 0.0,
    risk_cap: Optional[float] = None,
    asset: str = "BTC"
):
    """
    Mirror page showing Polymarket event with strategy calculations
    """
    event = await polymarket.parse_event_by_slug(slug)
    
    if not event:
        return templates.TemplateResponse(
            "error.html",
            {
                "request": request,
                "error": f"Could not load event: {slug}"
            }
        )
    
    if asset == "BTC":
        anchor = await binance.get_btc_price()
    elif asset == "ETH":
        anchor = await binance.get_eth_price()
    elif asset == "SOL":
        anchor = await binance.get_sol_price()
    else:
        anchor = 50000.0
    
    if not anchor:
        anchor = 50000.0
    
    orders, summary = engine.calculate_symmetric_strategy(
        markets=event.markets,
        anchor=anchor,
        budget=budget,
        bias=bias,
        risk_cap=risk_cap
    )
    
    return templates.TemplateResponse(
        "mirror.html",
        {
            "request": request,
            "event": event,
            "anchor": anchor,
            "asset": asset,
            "budget": budget,
            "bias": bias,
            "orders": orders,
            "summary": summary
        }
    )


@app.get("/api/spot-price")
async def get_spot_price(asset: str = "BTC"):
    """Get current spot price from Binance"""
    if asset == "BTC":
        price = await binance.get_btc_price()
    elif asset == "ETH":
        price = await binance.get_eth_price()
    elif asset == "SOL":
        price = await binance.get_sol_price()
    else:
        return JSONResponse({"error": "Invalid asset"}, status_code=400)
    
    if price is None:
        return JSONResponse({"error": "Failed to fetch price"}, status_code=500)
    
    return {"asset": asset, "price": price}


@app.post("/api/calculate")
async def calculate_strategy(
    slug: str = Form(...),
    budget: float = Form(1000.0),
    bias: float = Form(0.0),
    risk_cap: Optional[float] = Form(None),
    asset: str = Form("BTC")
):
    """Calculate strategy and return JSON"""
    event = await polymarket.parse_event_by_slug(slug)
    
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    
    if asset == "BTC":
        anchor = await binance.get_btc_price()
    elif asset == "ETH":
        anchor = await binance.get_eth_price()
    elif asset == "SOL":
        anchor = await binance.get_sol_price()
    else:
        anchor = 50000.0
    
    if not anchor:
        anchor = 50000.0
    
    orders, summary = engine.calculate_symmetric_strategy(
        markets=event.markets,
        anchor=anchor,
        budget=budget,
        bias=bias,
        risk_cap=risk_cap
    )
    
    return {
        "event": {
            "title": event.title,
            "slug": event.slug,
            "num_markets": len(event.markets)
        },
        "anchor": anchor,
        "orders": [
            {
                "market_id": o.market_id,
                "question": o.question,
                "strike": o.strike,
                "side": o.side,
                "units": o.units,
                "limit_price": o.limit_price,
                "cost": o.cost,
                "max_profit": o.max_profit,
                "max_loss": o.max_loss
            }
            for o in orders
        ],
        "summary": {
            "total_cost": summary.total_cost,
            "max_loss": summary.max_loss,
            "max_profit": summary.max_profit,
            "up_side_cost": summary.up_side_cost,
            "down_side_cost": summary.down_side_cost,
            "num_orders": summary.num_orders
        }
    }


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    await binance.close()
    await polymarket.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
