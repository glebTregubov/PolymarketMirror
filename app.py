from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Optional
import os

from binance_client import BinanceClient
from polymarket_parser import PolymarketParser
from strategy_engine import StrategyEngine


app = FastAPI(title="Polymarket Strategy Mirror")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

binance = BinanceClient()
polymarket = PolymarketParser()
engine = StrategyEngine()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main page with input form"""
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "example_slug": "will-bitcoin-hit-120k-by-dec-31-2025"
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
