import pytest
import pytest_asyncio

from polymarket_parser import PolymarketParser, ASSET_CONFIG


@pytest_asyncio.fixture
async def parser():
    instance = PolymarketParser()
    try:
        yield instance
    finally:
        await instance.close()


def _solana_weekly_event():
    return {
        "title": "What price will Solana hit October 13-19?",
        "slug": "what-price-will-solana-hit-october-13-19",
        "seriesSlug": "solana-hit-price-weekly",
        "ticker": "what-price-will-solana-hit-october-13-19",
        "description": "What price will Solana hit October 13-19?",
        "subtitle": None,
        "tags": [
            {"label": "Solana"},
            {"label": "Hit Price"},
            {"label": "Weekly"},
            {"label": "Crypto"},
            {"label": "Crypto Prices"},
            {"label": "Recurring"},
            {"label": "Hide From New"},
        ],
    }


def _solana_monthly_event():
    return {
        "title": "What price will Solana hit in October?",
        "slug": "what-price-will-solana-hit-in-october-452",
        "seriesSlug": "sol-monthly-prices",
        "ticker": "what-price-will-solana-hit-in-october-452",
        "description": "What price will Solana hit in October?",
        "subtitle": None,
        "tags": [
            {"label": "Solana"},
            {"label": "Monthly"},
            {"label": "Recurring"},
            {"label": "Crypto"},
            {"label": "Hit Price"},
            {"label": "Crypto Prices"},
        ],
    }


@pytest.mark.asyncio
async def test_solana_weekly_event_detected(parser: PolymarketParser):
    item = _solana_weekly_event()
    aliases = ASSET_CONFIG["SOL"]["aliases"]
    tag_labels = ASSET_CONFIG["SOL"]["tag_labels"]

    assert parser._matches_asset(item, aliases, tag_labels)
    assert parser._is_ladder_event(item, aliases)


@pytest.mark.asyncio
async def test_solana_monthly_event_detected(parser: PolymarketParser):
    item = _solana_monthly_event()
    aliases = ASSET_CONFIG["SOL"]["aliases"]
    tag_labels = ASSET_CONFIG["SOL"]["tag_labels"]

    assert parser._matches_asset(item, aliases, tag_labels)
    assert parser._is_ladder_event(item, aliases)


@pytest.mark.asyncio
async def test_search_terms_include_month_price(parser: PolymarketParser):
    terms = parser._candidate_search_terms(
        ["solana"],
        ["Solana", "SOL"]
    )
    lower_terms = {term.lower() for term in terms}
    assert "solana price october" in lower_terms
    assert "solana price" in lower_terms
