from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Optional, List, Dict, Tuple
from datetime import datetime, timedelta, timezone
import re
import os

from binance_client import BinanceClient
from polymarket_parser import PolymarketParser, Market, EventSummary
from strategy_engine import StrategyEngine


def strike_value(market: Market) -> Optional[float]:
    """Return numeric strike for a market; None when unavailable."""
    if not market or not market.strike:
        return None

    strike = market.strike.K
    if strike is None:
        return None

    try:
        return float(strike)
    except (TypeError, ValueError):
        return None


def split_markets_by_anchor(markets: List[Market], anchor: float) -> Tuple[List[Market], List[Market]]:
    """Split markets into upside/downside buckets sorted relative to anchor."""
    upside: List[Market] = []
    downside: List[Market] = []

    for market in markets:
        strike = strike_value(market)
        if strike is None:
            continue

        if strike > anchor:
            upside.append(market)
        else:
            downside.append(market)

    upside.sort(key=lambda market: strike_value(market), reverse=True)
    downside.sort(key=lambda market: strike_value(market), reverse=True)
    return upside, downside


app = FastAPI(title="Polymarket Strategy Mirror")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

_binance = None
_polymarket = None
_engine = None


def get_binance() -> BinanceClient:
    """Lazy-load Binance client"""
    global _binance
    if _binance is None:
        _binance = BinanceClient()
    return _binance


def get_polymarket() -> PolymarketParser:
    """Lazy-load Polymarket parser"""
    global _polymarket
    if _polymarket is None:
        _polymarket = PolymarketParser()
    return _polymarket


def get_engine() -> StrategyEngine:
    """Lazy-load Strategy engine"""
    global _engine
    if _engine is None:
        _engine = StrategyEngine()
    return _engine


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


def calculate_delta_neutral_pairs(markets: List[Market], anchor: float, slug: str = None) -> Dict[float, Dict]:
    """
    Calculate delta-neutral unit pairs for each strike.
    
    For ALL strikes: Buy NO at current strike + Buy YES at strike BELOW
    This creates delta-neutral units regardless of direction.
    
    Skip resolved markets (where YES=1.0 or NO=1.0)
    
    Returns: {strike: {'partner_strike': float, 'cost': float, 'pnl': float, 'yes_price': float, 'no_price': float, 'apy': float}}
    """
    pairs = {}
    
    # Calculate days to expiry for APY
    days_to_expiry = 7  # Default
    if slug:
        expiry = extract_expiry_from_slug(slug)
        if expiry:
            days_to_expiry = max(1, (expiry - datetime.now()).days)
    
    # Filter out resolved markets and sort by strike (ascending order)
    open_markets = sorted(
        [m for m in markets if m.strike and m.yes_price < 0.99 and m.no_price < 0.99],
        key=lambda m: m.strike.K
    )
    
    for i, market in enumerate(open_markets):
        strike = market.strike.K
        
        # For all strikes: Buy NO at current + Buy YES at strike BELOW
        if i > 0:
            partner = open_markets[i - 1]  # Lower strike (below)
            # Skip if partner is also resolved
            if partner.yes_price >= 0.99 or partner.no_price >= 0.99:
                continue
                
            cost = market.no_price + partner.yes_price
            pnl = 1.0 - cost
            apy = calculate_apy(pnl, cost, days_to_expiry)
            
            direction = 'downside' if strike <= anchor else 'upside'
            
            pairs[strike] = {
                'partner_strike': partner.strike.K,
                'cost': cost,
                'pnl': pnl,
                'no_price': market.no_price,
                'yes_price': partner.yes_price,
                'direction': direction,
                'apy': apy
            }
    
    return pairs


def calculate_apy(pnl: float, cost: float, days_to_expiry: int) -> float:
    """Calculate APY from PnL, cost, and days to expiry"""
    if cost <= 0 or days_to_expiry <= 0:
        return 0.0
    
    return_pct = (pnl / cost) * 100
    apy = return_pct * (365 / days_to_expiry)
    return apy


@app.get("/health")
async def health():
    """Health check endpoint for deployment"""
    return {"status": "ok", "service": "polymarket-strategy-mirror"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main landing page with event selection form"""
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "example_slug": "what-price-will-ethereum-hit-september-29-october-5"
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
        anchor = await get_binance().get_eth_price() or 4000.0
    elif asset == "SOL":
        anchor = await get_binance().get_sol_price() or 200.0
    else:
        anchor = await get_binance().get_btc_price() or 95000.0
    
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
    
    orders, summary = get_engine().calculate_symmetric_strategy(
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


@app.get("/events", response_class=HTMLResponse)
async def events_list(request: Request):
    """
    Display list of crypto ladder events available for analysis
    """
    events = await get_polymarket().get_crypto_events(assets=["BTC", "ETH", "SOL"])
    
    return templates.TemplateResponse(
        "events.html",
        {
            "request": request,
            "events": events
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
    event = await get_polymarket().parse_event_by_slug(slug)
    
    if not event:
        return templates.TemplateResponse(
            "error.html",
            {
                "request": request,
                "error": f"Could not load event: {slug}"
            }
        )
    
    if asset == "BTC":
        anchor = await get_binance().get_btc_price()
    elif asset == "ETH":
        anchor = await get_binance().get_eth_price()
    elif asset == "SOL":
        anchor = await get_binance().get_sol_price()
    else:
        anchor = 50000.0
    
    if not anchor:
        anchor = 50000.0
    
    orders, summary = get_engine().calculate_symmetric_strategy(
        markets=event.markets,
        anchor=anchor,
        budget=budget,
        bias=bias,
        risk_cap=risk_cap
    )
    
    pairs = calculate_delta_neutral_pairs(event.markets, anchor, slug)

    resolve_time_str = event.resolve_time
    if not resolve_time_str:
        for market in event.markets:
            if market.end_date:
                resolve_time_str = market.end_date
                break

    now_utc = datetime.now(timezone.utc)
    countdown = None
    if resolve_time_str:
        try:
            target_dt = datetime.fromisoformat(resolve_time_str.replace("Z", "+00:00"))
            if target_dt.tzinfo is None:
                target_dt = target_dt.replace(tzinfo=timezone.utc)
            delta = target_dt - now_utc
            if delta.total_seconds() < 0:
                delta = timedelta(0)
            days = delta.days
            hours, remainder = divmod(delta.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            countdown = {
                "days": f"{days:02d}",
                "hours": f"{hours:02d}",
                "minutes": f"{minutes:02d}",
                "target_iso": target_dt.isoformat()
            }
        except ValueError:
            countdown = None

    last_updated = now_utc.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    upside_markets, downside_markets = split_markets_by_anchor(event.markets, anchor)

    response = templates.TemplateResponse(
        "mirror.html",
        {
            "request": request,
            "event": event,
            "anchor": anchor,
            "asset": asset,
            "budget": budget,
            "bias": bias,
            "slug": slug,
            "risk_cap": risk_cap,
            "orders": orders,
            "summary": summary,
            "pairs": pairs,
            "upside_markets": upside_markets,
            "downside_markets": downside_markets,
            "countdown": countdown,
            "last_updated": last_updated
        }
    )

    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/spot-price")
async def get_spot_price(asset: str = "BTC"):
    """Get current spot price from Binance"""
    if asset == "BTC":
        price = await get_binance().get_btc_price()
    elif asset == "ETH":
        price = await get_binance().get_eth_price()
    elif asset == "SOL":
        price = await get_binance().get_sol_price()
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
    event = await get_polymarket().parse_event_by_slug(slug)
    
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    
    if asset == "BTC":
        anchor = await get_binance().get_btc_price()
    elif asset == "ETH":
        anchor = await get_binance().get_eth_price()
    elif asset == "SOL":
        anchor = await get_binance().get_sol_price()
    else:
        anchor = 50000.0
    
    if not anchor:
        anchor = 50000.0
    
    orders, summary = get_engine().calculate_symmetric_strategy(
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
    """Cleanup on shutdown - only close clients if they were initialized"""
    if _binance is not None:
        await _binance.close()
    if _polymarket is not None:
        await _polymarket.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
