import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import (  # noqa: E402
    ScenarioRequest,
    calculate_delta_neutral_pairs,
    format_cents_no_round,
    simulate_pair_scenario,
)
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


def test_simulate_pair_scenario_matches_anchor_prices():
    request = ScenarioRequest(
        asset="BTC",
        anchor=109000,
        yes_price=0.18,
        no_price=0.56,
        yes_units=2000,
        no_units=1000,
        yes_strike=105000,
        no_strike=107000,
        yes_label="Bitcoin 105k YES",
        no_label="Bitcoin 107k NO",
        pair_label="Pair Demo",
        direction="upside",
    )

    result = simulate_pair_scenario(request)
    assert len(result["prices"]) >= 3

    idx = result["highlight_index"]
    yes_anchor = result["rows"][0]["values"][idx]
    no_anchor = result["rows"][1]["values"][idx]

    assert yes_anchor == pytest.approx(0.18, abs=1e-3)
    assert no_anchor == pytest.approx(0.56, abs=1e-3)
    assert result["return_row"][idx] == pytest.approx(result["invested"], abs=1e-6)
    assert result["anchor_price"] == pytest.approx(request.anchor)


def test_generate_price_grid_rounds_to_clean_steps():
    request = ScenarioRequest(
        asset="ETH",
        anchor=3822,
        yes_price=0.45,
        no_price=0.55,
        yes_units=0,
        no_units=0,
        yes_strike=4000,
        no_strike=3600,
    )

    result = simulate_pair_scenario(request)
    prices = result["prices"]
    highlight_price = prices[result["highlight_index"]]

    assert 3700 in prices
    assert 3900 in prices
    assert highlight_price == 3800
    assert len(prices) <= 9
