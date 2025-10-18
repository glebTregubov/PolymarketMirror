import asyncio

from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Optional, List, Dict, Tuple, Any, Set
from datetime import datetime, timedelta, timezone
import math
import re
import os
from decimal import Decimal, ROUND_DOWN, InvalidOperation
import httpx

from pydantic import BaseModel

from binance_client import BinanceClient
from polymarket_parser import PolymarketParser, Market, EventSummary
from strategy_engine import StrategyEngine
from polymarket_orders import PolymarketOrdersClient


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


ASSET_PRICE_STEPS: Dict[str, float] = {
    "BTC": 1000.0,
    "ETH": 100.0,
    "SOL": 10.0,
    "XRP": 0.1
}


def format_cents_no_round(price: Optional[float]) -> str:
    """Convert price (0-1 range) to cents with one decimal place, truncating instead of rounding."""
    if price is None:
        return "0.0"

    try:
        cents = Decimal(str(price)) * Decimal("100")
    except (InvalidOperation, ValueError):
        return "0.0"

    truncated = cents.quantize(Decimal("0.1"), rounding=ROUND_DOWN)
    return format(truncated, "f")


def _clamp_probability(value: float) -> float:
    return max(0.001, min(0.999, value))


def logistic_probability(strike: float, anchor: float, spot: float, anchor_prob: float) -> float:
    """Project YES probability at a new spot using a logistic curve."""
    if strike is None:
        strike = anchor

    anchor_prob = _clamp_probability(anchor_prob)
    delta = strike - anchor
    if abs(delta) < 1e-6:
        delta = 1.0 if strike >= anchor else -1.0

    try:
        slope = math.log((1.0 / anchor_prob) - 1.0) / delta
    except (ValueError, ZeroDivisionError):
        slope = 0.0

    exponent = slope * (strike - spot)
    exponent = max(min(exponent, 60.0), -60.0)
    return 1.0 / (1.0 + math.exp(exponent))


def generate_price_grid(anchor: float, asset: str, percent: float = 0.05) -> List[float]:
    """Generate price ladder around anchor using asset-specific step size."""
    step = ASSET_PRICE_STEPS.get(asset.upper(), max(anchor * 0.01, 1.0))
    if step <= 0:
        step = max(anchor * 0.01, 1.0)

    span = max(1, min(4, math.ceil((anchor * percent) / step)))
    points: List[float] = []
    center = round(anchor / step) * step

    for offset in range(-span, span + 1):
        price = center + offset * step
        if price <= 0:
            continue
        if step < 1:
            price = round(price, 2)
        else:
            price = round(price)
        points.append(price)

    # Ensure anchor included and sorted unique
    if center > 0:
        if step < 1:
            center_val = round(center, 2)
        else:
            center_val = round(center)
        points.append(center_val)

    return sorted(set(points))


class ScenarioRequest(BaseModel):
    asset: str
    anchor: float
    yes_price: float
    no_price: float
    yes_units: int
    no_units: int
    yes_strike: Optional[float]
    no_strike: Optional[float]
    yes_label: Optional[str] = None
    no_label: Optional[str] = None
    pair_label: Optional[str] = None
    direction: Optional[str] = None


app = FastAPI(title="Polymarket Strategy Mirror")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
templates.env.filters["cents"] = format_cents_no_round

_binance = None
_polymarket = None
_orders_client = None
_engine = None

DEFAULT_POLYMARKET_ADDRESSES: List[str] = [
    os.getenv("POLYMARKET_PRIMARY_ADDRESS", "0x7a6603ba85992b6fa88ac8000a3f2169d2a45b1b"),
    os.getenv("POLYMARKET_CORRESPONDING_ADDRESS", "0x87269aECf0A06341D85E5ED3CfdbE494247f3202"),
]


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


def get_orders_client() -> PolymarketOrdersClient:
    """Lazy-load Polymarket orders client"""
    global _orders_client
    if _orders_client is None:
        _orders_client = PolymarketOrdersClient()
    return _orders_client


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
    
    total_markets = len(open_markets)
    for i, market in enumerate(open_markets):
        strike = market.strike.K
        direction = 'downside' if strike <= anchor else 'upside'

        if direction == 'downside':
            if i == 0:
                continue
            partner = open_markets[i - 1]
        else:
            if i >= total_markets - 1:
                continue
            partner = open_markets[i + 1]

        if not partner.strike:
            continue

        if partner.yes_price >= 0.99 or partner.no_price >= 0.99:
            continue

        yes_market = partner  # YES leg comes from adjacent strike in direction of the move
        no_market = market    # NO leg always uses the current strike

        cost = yes_market.yes_price + no_market.no_price
        pnl = 1.0 - cost
        apy = calculate_apy(pnl, cost, days_to_expiry)

        pairs[strike] = {
            'partner_strike': partner.strike.K,
            'cost': cost,
            'pnl': pnl,
            'no_price': no_market.no_price,
            'yes_price': yes_market.yes_price,
            'direction': direction,
            'apy': apy,
            'no_strike': no_market.strike.K if no_market.strike else None,
            'yes_strike': yes_market.strike.K if yes_market.strike else None,
        }
    
    return pairs


def simulate_pair_scenario(request: ScenarioRequest) -> Dict[str, Any]:
    """Simulate price ladder outcomes for a YES/NO pair using logistic projections."""
    prices = generate_price_grid(request.anchor, request.asset)
    yes_anchor_prob = _clamp_probability(request.yes_price)
    no_market_yes_prob = _clamp_probability(1.0 - request.no_price)

    yes_units = request.yes_units if request.yes_units else 200
    no_units = request.no_units if request.no_units else 100

    yes_values: List[float] = []
    no_values: List[float] = []
    returns: List[float] = []

    yes_strike = request.yes_strike if request.yes_strike is not None else request.anchor
    no_strike = request.no_strike if request.no_strike is not None else request.anchor

    for price in prices:
        yes_price_projected = logistic_probability(yes_strike, request.anchor, price, yes_anchor_prob)
        no_price_projected = 1.0 - logistic_probability(no_strike, request.anchor, price, no_market_yes_prob)

        yes_values.append(yes_price_projected)
        no_values.append(no_price_projected)

        total_value = (yes_units * yes_price_projected) + (no_units * no_price_projected)
        returns.append(total_value)

    invested = (yes_units * request.yes_price) + (no_units * request.no_price)
    highlight_index = min(range(len(prices)), key=lambda idx: abs(prices[idx] - request.anchor))

    yes_anchor_value = logistic_probability(yes_strike, request.anchor, request.anchor, yes_anchor_prob)
    no_anchor_value = 1.0 - logistic_probability(no_strike, request.anchor, request.anchor, no_market_yes_prob)
    yes_values[highlight_index] = yes_anchor_value
    no_values[highlight_index] = no_anchor_value
    returns[highlight_index] = (yes_units * yes_anchor_value) + (no_units * no_anchor_value)

    return {
        "prices": prices,
        "rows": [
            {
                "label": request.yes_label or "YES",
                "quantity": yes_units,
                "values": yes_values
            },
            {
                "label": request.no_label or "NO",
                "quantity": no_units,
                "values": no_values
            }
        ],
        "return_row": returns,
        "invested": invested,
        "highlight_index": highlight_index,
        "pair_label": request.pair_label,
        "direction": request.direction,
        "anchor_price": request.anchor
    }


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
async def events_list(request: Request, refresh: bool = Query(False)):
    """
    Display list of crypto ladder events available for analysis
    """
    events = await get_polymarket().get_crypto_events(
        assets=["BTC", "ETH", "SOL", "XRP"],
        force_refresh=refresh
    )

    refresh_params: Dict[str, Any] = dict(request.query_params)
    refresh_params["refresh"] = "true"

    header_refresh = {
        "action": request.url.path,
        "method": "get",
        "params": refresh_params
    }

    response = templates.TemplateResponse(
        "events.html",
        {
            "request": request,
            "events": events,
            "header_refresh": header_refresh
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _sanitize_addresses(addresses: Optional[List[str]]) -> List[str]:
    """Return normalized list of unique addresses."""
    seen = set()
    cleaned: List[str] = []
    source = addresses or DEFAULT_POLYMARKET_ADDRESSES
    for raw in source:
        if not raw:
            continue
        addr = raw.strip()
        if not addr:
            continue
        lower = addr.lower()
        if lower in seen:
            continue
        seen.add(lower)
        cleaned.append(addr)
    return cleaned


def _format_trade_timestamp(raw_ts: Any) -> Optional[str]:
    """Convert polymarket timestamp (seconds) to readable UTC string."""
    if raw_ts is None:
        return None
    try:
        ts = float(raw_ts)
    except (TypeError, ValueError):
        return None
    if ts > 1e12:  # millisecond safety
        ts = ts / 1000.0
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _as_float(value: Any) -> float:
    """Coerce numeric values to float with graceful fallback."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


def _summarize_positions(positions: List[Dict[str, Any]]) -> Dict[str, float]:
    """Aggregate key metrics for a set of positions."""
    total_initial = sum(
        _as_float(pos.get("initial_value", pos.get("initialValue"))) for pos in positions
    )
    total_current = sum(
        _as_float(pos.get("current_value", pos.get("currentValue"))) for pos in positions
    )
    total_cash_pnl = sum(
        _as_float(pos.get("cash_pnl", pos.get("cashPnl"))) for pos in positions
    )
    total_size = sum(_as_float(pos.get("size")) for pos in positions)
    return {
        "total_initial": total_initial,
        "total_current": total_current,
        "total_cash_pnl": total_cash_pnl,
        "total_size": total_size,
    }


def _extract_position_strike(position: Dict[str, Any]) -> Optional[float]:
    """Attempt to infer strike price from position metadata."""
    text_candidates = [
        position.get("title"),
        position.get("slug"),
        position.get("event_slug"),
        position.get("eventSlug"),
    ]
    parser = get_polymarket()
    for text in text_candidates:
        if not text:
            continue
        strike_meta = parser.extract_strike_from_text(str(text))
        if strike_meta and strike_meta.K:
            try:
                return float(strike_meta.K)
            except (TypeError, ValueError):
                continue
    for text in text_candidates:
        if not text:
            continue
        match = re.search(r"(?:\$|usd)?\s?(\d[\d,]*(?:\.\d+)?)", str(text), re.IGNORECASE)
        if match:
            try:
                return float(match.group(1).replace(",", ""))
            except ValueError:
                continue
    return None


def _parse_end_date(raw_value: Any) -> Optional[datetime]:
    """Parse a Polymarket end date string into a timezone-aware datetime."""
    if not raw_value:
        return None
    if isinstance(raw_value, datetime):
        dt = raw_value
    else:
        text = str(raw_value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = datetime.strptime(text, "%Y-%m-%d")
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_position_expired(position: Dict[str, Any], grace_days: int = 1) -> bool:
    """Determine whether a position has expired beyond the grace period."""
    end_date_value = position.get("end_date") or position.get("endDate")
    end_dt = _parse_end_date(end_date_value)
    if not end_dt:
        return False
    now = datetime.now(timezone.utc)
    grace = max(0, grace_days)
    cutoff = end_dt + timedelta(days=grace)
    return now > cutoff


def _prepare_position(position: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a raw position payload for template rendering."""
    strike = _extract_position_strike(position)
    avg_price = _as_float(position.get("avgPrice"))
    cur_price = _as_float(position.get("curPrice"))
    size = _as_float(position.get("size"))
    initial_value = _as_float(position.get("initialValue"))
    current_value = _as_float(position.get("currentValue"))

    return {
        "asset_id": position.get("asset"),
        "title": position.get("title"),
        "slug": position.get("slug"),
        "event_slug": position.get("eventSlug"),
        "outcome": position.get("outcome"),
        "strike": strike,
        "size": size,
        "avg_price": avg_price,
        "avg_price_cents": avg_price * 100.0,
        "cur_price": cur_price,
        "cur_price_cents": cur_price * 100.0,
        "initial_value": initial_value,
        "current_value": current_value,
        "cash_pnl": current_value - initial_value,
        "percent_pnl": position.get("percentPnl"),
        "end_date": position.get("endDate"),
        "bet_amount": initial_value,
        "to_win": size,
        "icon": position.get("icon"),
        "redeemable": bool(position.get("redeemable")),
        "negative_risk": bool(position.get("negativeRisk")),
    }


def _prepare_trade(trade: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a raw trade payload for template rendering."""
    size = _as_float(trade.get("size"))
    price = _as_float(trade.get("price"))
    return {
        "title": trade.get("title"),
        "slug": trade.get("slug"),
        "event_slug": trade.get("eventSlug"),
        "outcome": trade.get("outcome"),
        "side": trade.get("side"),
        "size": size,
        "price": price,
        "value": size * price,
        "timestamp_raw": trade.get("timestamp"),
        "timestamp": _format_trade_timestamp(trade.get("timestamp")),
        "transaction_hash": trade.get("transactionHash"),
        "icon": trade.get("icon"),
    }


def _position_segment(position: Dict[str, Any], size: float) -> Dict[str, Any]:
    """Return proportional snapshot of a position for a given size."""
    size = max(0.0, size)
    avg_price = position["avg_price"]
    cur_price = position["cur_price"]
    cost = size * avg_price
    current_value = size * cur_price
    cash_pnl = current_value - cost
    percent_pnl = None
    if cost > 0:
        percent_pnl = (cash_pnl / cost) * 100.0

    return {
        "asset_id": position.get("asset_id"),
        "title": position.get("title"),
        "slug": position.get("slug"),
        "event_slug": position.get("event_slug"),
        "outcome": position.get("outcome"),
        "strike": position.get("strike"),
        "size": size,
        "avg_price": avg_price,
        "cost": cost,
        "cur_price": cur_price,
        "current_value": current_value,
        "cash_pnl": cash_pnl,
        "percent_pnl": percent_pnl,
        "initial_value": cost,
    }


def _make_leftover_entry(position: Dict[str, Any], size: float) -> Dict[str, Any]:
    """Return simplified leftover structure for unmatched positions."""
    return {
        "outcome": position.get("outcome"),
        "strike": position.get("strike"),
        "size": max(0.0, size),
        "avg_price": position.get("avg_price"),
    }


def _pair_positions_for_event(
    event_slug: str,
    positions: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Pair NO positions with the nearest lower YES positions to form unit groupings.
    Returns (units, leftovers).
    """
    yes_entries: List[Dict[str, Any]] = []
    no_entries: List[Dict[str, Any]] = []
    leftovers: List[Dict[str, Any]] = []

    for pos in positions:
        size = _as_float(pos.get("size"))
        strike = pos.get("strike")
        if size <= 0 or strike is None:
            leftovers.append(_make_leftover_entry(pos, size))
            continue

        entry = {
            "position": pos,
            "remaining": size,
            "strike": strike,
        }
        outcome = str(pos.get("outcome")).lower()
        if outcome == "yes":
            yes_entries.append(entry)
        elif outcome == "no":
            no_entries.append(entry)
        else:
            leftovers.append(_make_leftover_entry(pos, size))

    yes_entries.sort(key=lambda item: item["strike"])
    no_entries.sort(key=lambda item: item["strike"])

    units: List[Dict[str, Any]] = []

    for no_entry in no_entries:
        while no_entry["remaining"] > 0:
            candidate_index = None
            for idx in range(len(yes_entries) - 1, -1, -1):
                yes_entry = yes_entries[idx]
                if yes_entry["remaining"] <= 0:
                    continue
                if yes_entry["strike"] is None:
                    continue
                if yes_entry["strike"] < no_entry["strike"]:
                    candidate_index = idx
                    break

            if candidate_index is not None:
                yes_entry = yes_entries[candidate_index]
                paired_size = min(no_entry["remaining"], yes_entry["remaining"])
                yes_segment = _position_segment(yes_entry["position"], paired_size)
                yes_entry["remaining"] -= paired_size
            else:
                paired_size = no_entry["remaining"]
                yes_segment = None

            if paired_size <= 0:
                break

            no_segment = _position_segment(no_entry["position"], paired_size)
            no_entry["remaining"] -= paired_size

            total_invested = (no_segment["cost"] if no_segment else 0.0) + (yes_segment["cost"] if yes_segment else 0.0)
            total_current = (no_segment["current_value"] if no_segment else 0.0) + (yes_segment["current_value"] if yes_segment else 0.0)
            current_pnl = total_current - total_invested
            current_pnl_pct = (current_pnl / total_invested * 100.0) if total_invested > 0 else None

            expiration_total = no_segment["size"] if no_segment else 0.0
            expiration_profit = expiration_total - total_invested
            expiration_profit_pct = (expiration_profit / total_invested * 100.0) if total_invested > 0 else None

            units.append({
                "event_slug": event_slug,
                "no": no_segment,
                "yes": yes_segment,
                "total_invested": total_invested,
                "total_current_value": total_current,
                "current_pnl": current_pnl,
                "current_pnl_pct": current_pnl_pct,
                "expiration_total": expiration_total,
                "expiration_profit": expiration_profit,
                "expiration_profit_pct": expiration_profit_pct,
            })

            if candidate_index is None:
                break

    for yes_entry in yes_entries:
        remaining_yes = yes_entry.get("remaining", 0.0)
        if remaining_yes <= 0:
            continue

        yes_segment = _position_segment(yes_entry["position"], remaining_yes)
        total_invested = yes_segment["cost"]
        total_current = yes_segment["current_value"]
        current_pnl = total_current - total_invested
        current_pnl_pct = (current_pnl / total_invested * 100.0) if total_invested > 0 else None
        expiration_total = 0.0
        expiration_profit = -total_invested
        expiration_profit_pct = (expiration_profit / total_invested * 100.0) if total_invested > 0 else None

        units.append({
            "event_slug": event_slug,
            "no": None,
            "yes": yes_segment,
            "total_invested": total_invested,
            "total_current_value": total_current,
            "current_pnl": current_pnl,
            "current_pnl_pct": current_pnl_pct,
            "expiration_total": expiration_total,
            "expiration_profit": expiration_profit,
            "expiration_profit_pct": expiration_profit_pct,
        })
        yes_entry["remaining"] = 0.0

    units.sort(
        key=lambda unit: (_unit_reference_strike(unit) if _unit_reference_strike(unit) is not None else float("-inf")),
        reverse=True
    )

    return units, leftovers


def _unit_reference_strike(unit: Dict[str, Any]) -> Optional[float]:
    no_segment = unit.get("no")
    if no_segment and no_segment.get("strike") is not None:
        return no_segment["strike"]
    yes_segment = unit.get("yes")
    if yes_segment and yes_segment.get("strike") is not None:
        return yes_segment["strike"]
    return None


async def _build_event_unit_tables(
    positions: List[Dict[str, Any]]
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, float]]:
    """Build per-event unit tables with summaries."""
    positions_by_event: Dict[str, List[Dict[str, Any]]] = {}
    for pos in positions:
        event_slug = pos.get("event_slug")
        if not event_slug:
            continue
        positions_by_event.setdefault(event_slug, []).append(pos)

    event_tables: Dict[str, Dict[str, Any]] = {}
    overall = {
        "total_invested": 0.0,
        "total_current_value": 0.0,
        "total_cash_pnl": 0.0,
        "expiration_total": 0.0,
        "expiration_profit": 0.0,
        "num_units": 0,
    }

    assets_needed: Dict[str, str] = {}

    for event_slug, event_positions in positions_by_event.items():
        units, leftovers = _pair_positions_for_event(event_slug, event_positions)
        summary = _summarize_units(units)
        asset = _infer_asset_from_slug(event_slug)
        if asset:
            assets_needed[event_slug] = asset

        overall["total_invested"] += summary["total_invested"]
        overall["total_current_value"] += summary["total_current_value"]
        overall["total_cash_pnl"] += summary["total_cash_pnl"]
        overall["expiration_total"] += summary["expiration_total"]
        overall["expiration_profit"] += summary["expiration_profit"]
        overall["num_units"] += summary["num_units"]

        event_tables[event_slug] = {
            "event_slug": event_slug,
            "event_label": _format_event_name(event_slug),
            "asset": asset,
            "units": units,
            "leftovers": leftovers,
            "summary": summary,
            "anchor_price": None,
        }

    if assets_needed:
        asset_prices = await _fetch_anchor_prices(set(assets_needed.values()))
        for event_slug, asset in assets_needed.items():
            event_tables[event_slug]["anchor_price"] = asset_prices.get(asset)
        for event in event_tables.values():
            anchor = event.get("anchor_price")
            for unit in event["units"]:
                ref_strike = _unit_reference_strike(unit)
                if anchor and ref_strike is not None:
                    unit["direction"] = "upside" if ref_strike > anchor else "downside"
                else:
                    unit["direction"] = None

    if overall["total_invested"] > 0:
        overall["current_pnl_pct"] = (overall["total_cash_pnl"] / overall["total_invested"]) * 100.0
        overall["expiration_profit_pct"] = (overall["expiration_profit"] / overall["total_invested"]) * 100.0
    else:
        overall["current_pnl_pct"] = None
        overall["expiration_profit_pct"] = None

    return event_tables, overall


def _summarize_units(units: List[Dict[str, Any]]) -> Dict[str, float]:
    total_invested = sum(unit["total_invested"] for unit in units)
    total_current = sum(unit["total_current_value"] for unit in units)
    total_cash_pnl = total_current - total_invested
    expiration_total = sum(unit["expiration_total"] for unit in units)
    expiration_profit = expiration_total - total_invested
    return {
        "total_invested": total_invested,
        "total_current_value": total_current,
        "total_cash_pnl": total_cash_pnl,
        "expiration_total": expiration_total,
        "expiration_profit": expiration_profit,
        "num_units": len(units),
        "current_pnl_pct": (total_cash_pnl / total_invested * 100.0) if total_invested > 0 else None,
        "expiration_profit_pct": (expiration_profit / total_invested * 100.0) if total_invested > 0 else None,
    }


ASSET_KEYWORD_MAP = {
    "bitcoin": "BTC",
    "btc": "BTC",
    "ethereum": "ETH",
    "eth": "ETH",
    "solana": "SOL",
    "sol": "SOL",
    "xrp": "XRP",
    "ripple": "XRP",
}


def _infer_asset_from_slug(slug: str) -> Optional[str]:
    if not slug:
        return None
    lowered = slug.lower()
    for keyword, asset in ASSET_KEYWORD_MAP.items():
        if keyword in lowered:
            return asset
    return None


def _format_event_name(slug: str) -> str:
    if not slug:
        return "Unknown event"
    words = slug.replace("-", " ").split()
    formatted = " ".join(word.capitalize() for word in words if word)
    return formatted or slug


async def _fetch_anchor_prices(assets: Set[str]) -> Dict[str, Optional[float]]:
    if not assets:
        return {}

    binance = get_binance()
    asset_list = sorted(assets)
    tasks = []

    for asset in asset_list:
        if asset == "BTC":
            tasks.append(binance.get_btc_price())
        elif asset == "ETH":
            tasks.append(binance.get_eth_price())
        elif asset == "SOL":
            tasks.append(binance.get_sol_price())
        elif asset == "XRP":
            tasks.append(binance.get_xrp_price())
        else:
            tasks.append(asyncio.sleep(0, result=None))  # placeholder

    results = await asyncio.gather(*tasks, return_exceptions=True)
    anchor_prices: Dict[str, Optional[float]] = {}
    for asset, result in zip(asset_list, results):
        if isinstance(result, Exception):
            anchor_prices[asset] = None
        else:
            anchor_prices[asset] = result
    return anchor_prices


@app.get("/orders", response_class=HTMLResponse)
async def orders_page(
    request: Request,
    addresses: Optional[List[str]] = Query(None),
    limit: int = Query(25, ge=1, le=200),
) -> HTMLResponse:
    """
    Display Polymarket stake information for configured addresses.
    """
    target_addresses = _sanitize_addresses(addresses)

    async def load_address_data(address: str) -> Dict[str, Any]:
        client = get_orders_client()
        async_tasks = await asyncio.gather(
            client.get_positions(address, limit=limit),
            client.get_trades(address, limit=limit),
            client.get_usdc_balance(address),
            return_exceptions=True,
        )

        positions_result, trades_result, cash_result = async_tasks

        if isinstance(positions_result, httpx.HTTPStatusError):
            status = positions_result.response.status_code if positions_result.response else "unknown"
            reason = positions_result.response.reason_phrase if positions_result.response else "request failed"
            error_message = f"HTTP {status}: {reason}"
            return {
                "address": address,
                "error": error_message,
                "positions": [],
                "trades": [],
                "summary": None,
                "filtered_expired": 0,
                "cash_balance": 0.0,
                "portfolio_value": 0.0,
                "units": [],
                "unit_leftovers": [],
                "unit_summary": {
                    "total_invested": 0.0,
                    "total_current_value": 0.0,
                    "total_cash_pnl": 0.0,
                    "num_units": 0,
                },
            }
        if isinstance(positions_result, Exception):
            return {
                "address": address,
                "error": str(positions_result),
                "positions": [],
                "trades": [],
                "summary": None,
                "filtered_expired": 0,
                "cash_balance": 0.0,
                "portfolio_value": 0.0,
                "units": [],
                "unit_leftovers": [],
                "unit_summary": {
                    "total_invested": 0.0,
                    "total_current_value": 0.0,
                    "total_cash_pnl": 0.0,
                    "num_units": 0,
                },
            }

        if isinstance(trades_result, Exception):
            trades_raw: List[Dict[str, Any]] = []
        else:
            trades_raw = trades_result

        cash_balance = 0.0
        if isinstance(cash_result, Exception):
            cash_balance = 0.0
        else:
            cash_balance = float(cash_result or 0.0)

        positions_raw = positions_result

        filtered_positions: List[Dict[str, Any]] = []
        expired_count = 0
        for raw_position in positions_raw:
            prepared = _prepare_position(raw_position)
            if _is_position_expired(prepared):
                expired_count += 1
                continue
            filtered_positions.append(prepared)

        positions_sorted = sorted(
            filtered_positions,
            key=lambda item: item["current_value"],
            reverse=True,
        )
        trades_sorted = sorted(
            (_prepare_trade(trade) for trade in trades_raw),
            key=lambda item: _as_float(item["timestamp_raw"]),
            reverse=True,
        )
        summary = _summarize_positions(positions_sorted)
        summary["cash_balance"] = cash_balance
        summary["portfolio_value"] = summary["total_current"] + cash_balance
        event_tables, unit_summary_overall = await _build_event_unit_tables(positions_sorted)
        summary["unit_invested"] = unit_summary_overall["total_invested"]
        summary["unit_current_value"] = unit_summary_overall["total_current_value"]
        summary["unit_cash_pnl"] = unit_summary_overall["total_cash_pnl"]
        summary["num_units"] = unit_summary_overall["num_units"]
        summary["unit_current_pnl_pct"] = unit_summary_overall.get("current_pnl_pct")
        summary["unit_expiration_total"] = unit_summary_overall["expiration_total"]
        summary["unit_expiration_profit"] = unit_summary_overall["expiration_profit"]
        summary["unit_expiration_profit_pct"] = unit_summary_overall.get("expiration_profit_pct")
        return {
            "address": address,
            "positions": positions_sorted,
            "trades": trades_sorted,
            "summary": summary,
            "filtered_expired": expired_count,
            "cash_balance": cash_balance,
            "portfolio_value": summary["portfolio_value"],
            "event_tables": list(event_tables.values()),
            "unit_summary": unit_summary_overall,
        }

    address_payloads: List[Dict[str, Any]] = []
    if target_addresses:
        address_payloads = await asyncio.gather(*(load_address_data(addr) for addr in target_addresses))

    aggregate_summary = _summarize_positions(
        [pos for payload in address_payloads for pos in payload.get("positions", [])]
    )
    total_cash = sum(_as_float(payload.get("cash_balance")) for payload in address_payloads)
    aggregate_summary["cash_balance"] = total_cash
    aggregate_summary["portfolio_value"] = aggregate_summary["total_current"] + total_cash
    aggregate_summary["unit_invested"] = sum(
        _as_float(payload.get("unit_summary", {}).get("total_invested")) for payload in address_payloads
    )
    aggregate_summary["unit_current_value"] = sum(
        _as_float(payload.get("unit_summary", {}).get("total_current_value")) for payload in address_payloads
    )
    aggregate_summary["unit_cash_pnl"] = sum(
        _as_float(payload.get("unit_summary", {}).get("total_cash_pnl")) for payload in address_payloads
    )
    aggregate_summary["unit_expiration_total"] = sum(
        _as_float(payload.get("unit_summary", {}).get("expiration_total")) for payload in address_payloads
    )
    aggregate_summary["unit_expiration_profit"] = sum(
        _as_float(payload.get("unit_summary", {}).get("expiration_profit")) for payload in address_payloads
    )
    aggregate_summary["num_units"] = int(
        sum(_as_float(payload.get("unit_summary", {}).get("num_units")) for payload in address_payloads)
    )
    if aggregate_summary["unit_invested"] > 0:
        aggregate_summary["unit_current_pnl_pct"] = (
            aggregate_summary["unit_cash_pnl"] / aggregate_summary["unit_invested"] * 100.0
        )
        aggregate_summary["unit_expiration_profit_pct"] = (
            aggregate_summary["unit_expiration_profit"] / aggregate_summary["unit_invested"] * 100.0
        )
    else:
        aggregate_summary["unit_current_pnl_pct"] = None
        aggregate_summary["unit_expiration_profit_pct"] = None

    refresh_params: Dict[str, Any] = dict(request.query_params)
    header_refresh = {
        "action": request.url.path,
        "method": "get",
        "params": refresh_params,
    }

    response = templates.TemplateResponse(
        "orders.html",
        {
            "request": request,
            "addresses": target_addresses,
            "address_payloads": address_payloads,
            "aggregate_summary": aggregate_summary,
            "limit": limit,
            "last_updated": datetime.now(timezone.utc),
            "header_refresh": header_refresh,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/mirror", response_class=HTMLResponse)
async def mirror(
    request: Request,
    slug: str,
    budget: float = 1000.0,
    bias: float = 0.0,
    risk_cap: Optional[float] = None,
    asset: str = "BTC",
    refresh: bool = Query(False)
):
    """
    Mirror page showing Polymarket event with strategy calculations
    """
    event = await get_polymarket().parse_event_by_slug(slug, force_refresh=refresh)
    
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
    elif asset == "XRP":
        anchor = await get_binance().get_xrp_price()
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

    header_refresh = {
        "action": request.url.path,
        "method": "get",
        "params": {
            "slug": slug,
            "asset": asset,
            "budget": budget,
            "bias": bias,
            "refresh": "true"
        }
    }

    if risk_cap is not None:
        header_refresh["params"]["risk_cap"] = risk_cap

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
            "last_updated": last_updated,
            "header_refresh": header_refresh
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


@app.post("/api/pair-scenario")
async def pair_scenario(request: ScenarioRequest):
    """Return projected price ladder for a selected YES/NO combination."""
    data = simulate_pair_scenario(request)
    return data


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
    elif asset == "XRP":
        anchor = await get_binance().get_xrp_price()
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
    if _orders_client is not None:
        await _orders_client.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
