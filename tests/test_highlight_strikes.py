from polymarket_parser import Market, StrikeMeta

from app import identify_highlight_strikes


def build_market(strike_value: float) -> Market:
    strike = StrikeMeta(raw=str(strike_value), K=strike_value, unit="USD")
    return Market(
        id=str(strike_value),
        question=f"Strike {strike_value}",
        outcome_type="binary",
        strike=strike,
        yes_price=0.5,
        no_price=0.5,
        spread=0.01,
    )


def test_highlight_requires_gap_and_threshold():
    markets = [
        build_market(90),
        build_market(95),
        build_market(105),
        build_market(110),
    ]

    pairs = {
        90.0: {"pnl": 0.12},
        95.0: {"pnl": 0.12},
        105.0: {"pnl": 0.12},
        110.0: {"pnl": 0.05},
    }

    highlights = identify_highlight_strikes(markets, pairs, anchor=100.0)

    assert highlights == {90.0}


def test_no_highlight_when_only_adjacent():
    markets = [
        build_market(110),
    ]
    pairs = {110.0: {"pnl": 0.25}}

    highlights = identify_highlight_strikes(markets, pairs, anchor=100.0)

    assert highlights == set()
