import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import split_markets_by_anchor, strike_value  # noqa: E402
from polymarket_parser import Market, StrikeMeta  # noqa: E402


def build_market(strike_value_usd):
    strike_meta = StrikeMeta(raw=str(strike_value_usd), K=strike_value_usd, unit="USD")
    return Market(
        id=str(strike_value_usd),
        question=f"Strike {strike_value_usd}",
        outcome_type="binary",
        strike=strike_meta,
        yes_price=0.5,
        no_price=0.5,
        spread=0.02,
    )


def test_upside_and_downside_are_sorted_relative_to_anchor():
    markets = [
        build_market(130000),
        build_market(100000),
        build_market(120000),
        build_market(110000),
    ]

    upside, downside = split_markets_by_anchor(markets, anchor=113000)

    assert [m.strike.K for m in upside] == [130000, 120000]
    assert [m.strike.K for m in downside] == [110000, 100000]


def test_strike_value_handles_missing_or_invalid_data():
    market_without_strike = Market(
        id="no-strike",
        question="No strike",
        outcome_type="binary",
        strike=None,
        yes_price=0.5,
        no_price=0.5,
        spread=0.02,
    )

    invalid_strike = Market(
        id="invalid",
        question="Invalid strike",
        outcome_type="binary",
        strike=StrikeMeta(raw="invalid", K=None, unit="USD"),
        yes_price=0.5,
        no_price=0.5,
        spread=0.02,
    )

    assert strike_value(market_without_strike) is None
    assert strike_value(invalid_strike) is None

    upside, downside = split_markets_by_anchor([market_without_strike, invalid_strike], anchor=113000)
    assert upside == []
    assert downside == []


def test_anchor_includes_equal_strikes_in_downside_bucket():
    markets = [
        build_market(113000),
        build_market(113500),
        build_market(112000),
    ]

    upside, downside = split_markets_by_anchor(markets, anchor=113000)

    assert [m.strike.K for m in upside] == [113500]
    assert [m.strike.K for m in downside] == [113000, 112000]


def test_parse_markets_skips_closed_markets():
    from polymarket_parser import PolymarketParser

    parser = PolymarketParser()
    markets_data = [
        {
            "id": "1",
            "question": "Will ETH reach $4,200?",
            "outcomePrices": [0.2, 0.8],
            "spread": 0.02,
            "market_type": "binary",
            "liquidity": 100,
            "acceptingOrders": False,
        },
        {
            "id": "2",
            "question": "Will ETH reach $4,300?",
            "outcomePrices": [0.3, 0.7],
            "spread": 0.02,
            "market_type": "binary",
            "liquidity": 150,
            "acceptingOrders": True,
        },
    ]

    markets = parser._parse_markets_from_json(markets_data)
    strikes = [m.strike.K for m in markets]

    assert strikes == [4300.0]
