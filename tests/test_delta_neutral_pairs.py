import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import calculate_delta_neutral_pairs, format_cents_no_round  # noqa: E402
from polymarket_parser import Market, StrikeMeta  # noqa: E402


def build_market(strike_usd: float, yes_price: float, no_price: float) -> Market:
    strike_meta = StrikeMeta(raw=str(strike_usd), K=strike_usd, unit="USD")
    return Market(
        id=f"market_{strike_usd}",
        question=f"Strike {strike_usd}",
        outcome_type="binary",
        strike=strike_meta,
        yes_price=yes_price,
        no_price=no_price,
        spread=0.02,
    )


def test_delta_neutral_pairs_flip_yes_source_for_upside_strikes():
    markets = [
        build_market(900, yes_price=0.22, no_price=0.78),
        build_market(1000, yes_price=0.35, no_price=0.65),
        build_market(1100, yes_price=0.28, no_price=0.72),
        build_market(1200, yes_price=0.20, no_price=0.80),
    ]

    pairs = calculate_delta_neutral_pairs(markets, anchor=1000)

    downside_pair = pairs[1000]
    assert downside_pair["direction"] == "downside"
    assert downside_pair["yes_price"] == pytest.approx(0.22)
    assert downside_pair["no_price"] == pytest.approx(0.65)
    assert downside_pair["yes_strike"] == pytest.approx(900)
    assert downside_pair["no_strike"] == pytest.approx(1000)
    assert downside_pair["cost"] == pytest.approx(0.87)

    upside_pair = pairs[1100]
    assert upside_pair["direction"] == "upside"
    assert upside_pair["yes_price"] == pytest.approx(0.20)
    assert upside_pair["no_price"] == pytest.approx(0.72)
    assert upside_pair["yes_strike"] == pytest.approx(1200)
    assert upside_pair["no_strike"] == pytest.approx(1100)
    assert upside_pair["cost"] == pytest.approx(0.92)


def test_format_cents_no_round_truncates_without_rounding():
    assert format_cents_no_round(0.163) == "16.3"
    assert format_cents_no_round(0.1667) == "16.6"
    assert format_cents_no_round(None) == "0.0"
