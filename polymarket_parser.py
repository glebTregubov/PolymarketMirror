import httpx
import json
import re
import time
from bs4 import BeautifulSoup
from typing import List, Optional, Dict, Any, Callable
from dataclasses import dataclass, field


ASSET_CONFIG: Dict[str, Dict[str, Any]] = {
    "BTC": {
        "queries": ["bitcoin"],
        "aliases": ["bitcoin", "btc"],
        "tag_labels": ["bitcoin"],
        "series_slugs": [
            "btc-multi-strikes-weekly",
            "bitcoin-neg-risk-weekly",
            "btc-monthly-prices",
        ],
    },
    "ETH": {
        "queries": ["ethereum"],
        "aliases": ["ethereum", "eth"],
        "tag_labels": ["ethereum"],
        "series_slugs": [
            "ethereum-multi-strikes-weekly",
            "ethereum-neg-risk-weekly",
            "eth-monthly-prices",
        ],
    },
    "SOL": {
        "queries": ["solana"],
        "aliases": ["solana", "sol"],
        "tag_labels": ["solana"],
        "series_slugs": [
            "solana-multi-strikes-weekly",
            "solana-neg-risk-weekly",
            "solana-monthly-prices",
        ],
    },
    "XRP": {
        "queries": ["ripple", "xrp"],
        "aliases": ["ripple", "xrp"],
        "tag_labels": ["ripple", "xrp"],
        "series_slugs": [
            "xrp-multi-strikes-weekly",
            "xrp-neg-risk-weekly",
            "xrp-monthly-prices",
        ],
    },
}

LADDER_KEYWORDS = [
    "what price will",
    "price on",
    "price be on",
    "price be at",
    "price hit",
    "price will",
    "above",
]

MONTH_KEYWORDS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]


@dataclass
class StrikeMeta:
    raw: str
    K: float
    unit: str


@dataclass
class Market:
    id: str
    question: str
    outcome_type: str
    strike: Optional[StrikeMeta]
    yes_price: float
    no_price: float
    spread: float
    liquidity: Optional[float] = None
    end_date: Optional[str] = None


@dataclass
class Event:
    id: str
    title: str
    description: str
    slug: str
    markets: List[Market]
    resolve_time: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    series_slug: Optional[str] = None
    volume: Optional[float] = None


@dataclass
class EventSummary:
    """Lightweight event summary for listing pages"""
    title: str
    slug: str
    asset: str  # BTC, ETH, SOL
    volume: Optional[str] = None
    num_markets: int = 0


class PolymarketParser:
    def __init__(self, base_url: str = "https://polymarket.com"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
        )
        self._build_id: Optional[str] = None

    def extract_strike_from_text(self, text: str) -> Optional[StrikeMeta]:
        """Extract numerical strike from market question text."""
        patterns = [
            r'\$?\s?(\d+[\d,]*\.?\d*)\s?(k|K)(?!\w)',
            r'\$?\s?(\d+[\d,]*\.?\d*)\s?(m|M)(?!\w)',
            r'\$\s?(\d+[\d,]*)',
            r'(\d+[\d,]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue

            raw = match.group(0)
            num_str = match.group(1).replace(',', '')
            num = float(num_str)

            if len(match.groups()) > 1:
                multiplier = match.group(2)
                if multiplier and multiplier.lower() == 'k':
                    num *= 1000
                    unit = "KUSD"
                elif multiplier and multiplier.lower() == 'm':
                    num *= 1000000
                    unit = "USD"
                else:
                    unit = "USD"
            else:
                unit = "USD"

            return StrikeMeta(raw=raw, K=num, unit=unit)

        return None

    async def parse_event_by_slug(self, slug: str, force_refresh: bool = False) -> Optional[Event]:
        """Parse a Polymarket event page by slug, falling back to HTML scraping if needed."""
        url = f"{self.base_url}/event/{slug}"
        params = {"_ts": int(time.time())} if force_refresh else None
        headers = self._no_cache_headers() if force_refresh else None

        try:
            response = await self.client.get(url, params=params, headers=headers)
            response.raise_for_status()
        except Exception as exc:
            print(f"Error fetching Polymarket event {slug}: {exc}")
            return None

        soup = BeautifulSoup(response.text, 'lxml')
        next_data = self._load_next_data(soup)

        if next_data:
            event_data = self._extract_event_data(next_data)
            if isinstance(event_data, dict) and event_data:
                return self._build_event_from_data(slug, event_data)

        # Fallback HTML parsing in case __NEXT_DATA__ is unavailable or malformed
        title = self._extract_title(soup)
        description = self._extract_description(soup)
        markets = self._extract_markets(soup)

        return Event(
            id=slug,
            title=title or slug,
            description=description or "",
            slug=slug,
            markets=markets
        )

    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract event title from page"""
        title_tag = soup.find('h1')
        if title_tag:
            return title_tag.get_text(strip=True)

        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            content = og_title.get('content')
            return str(content) if content else None

        return None

    def _extract_description(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract event description"""
        og_desc = soup.find('meta', property='og:description')
        if og_desc and og_desc.get('content'):
            content = og_desc.get('content')
            return str(content) if content else None
        return None

    def _parse_markets_from_json(self, markets_data: List[Dict[str, Any]]) -> List[Market]:
        """Parse markets from JSON data embedded in __NEXT_DATA__."""
        markets: List[Market] = []

        for market_data in markets_data:
            question = market_data.get('question', '')
            strike = self.extract_strike_from_text(question)

            outcome_prices = market_data.get('outcomePrices', [])
            yes_price = 0.5
            no_price = 0.5

            if outcome_prices and len(outcome_prices) >= 2:
                yes_price = float(outcome_prices[0]) if outcome_prices[0] else 0.5
                no_price = float(outcome_prices[1]) if outcome_prices[1] else 0.5

            spread = float(market_data.get('spread', 0.02))
            liquidity_raw = market_data.get('liquidityNum') or market_data.get('liquidity')
            liquidity = float(liquidity_raw) if liquidity_raw not in (None, "") else None
            end_date = (
                market_data.get('endDate')
                or market_data.get('end_date')
                or market_data.get('endDateIso')
            )

            if market_data.get('closed') or market_data.get('acceptingOrders') is False:
                continue

            market = Market(
                id=str(market_data.get('id', f"market_{len(markets)}")),
                question=question,
                outcome_type=market_data.get('market_type', market_data.get('outcomeType', 'binary')),
                strike=strike,
                yes_price=yes_price,
                no_price=no_price,
                spread=spread,
                liquidity=liquidity,
                end_date=end_date,
            )

            if strike:
                markets.append(market)

        return markets

    def _extract_markets(self, soup: BeautifulSoup) -> List[Market]:
        """Fallback market extraction using basic HTML heuristics."""
        markets: List[Market] = []

        market_elements = soup.find_all('div', class_=re.compile(r'market|outcome', re.I))

        if not market_elements:
            market_elements = soup.find_all('a', href=re.compile(r'/event/'))

        for idx, elem in enumerate(market_elements[:20]):
            text = elem.get_text(strip=True)

            if len(text) < 5 or len(text) > 300:
                continue

            strike = self.extract_strike_from_text(text)

            yes_price = 0.5
            no_price = 0.5

            market = Market(
                id=f"market_{idx}",
                question=text[:200],
                outcome_type="binary",
                strike=strike,
                yes_price=yes_price,
                no_price=no_price,
                spread=0.02
            )

            if strike:
                markets.append(market)

        return markets

    async def get_crypto_events(
        self,
        assets: List[str] = ("BTC", "ETH", "SOL"),
        force_refresh: bool = False
    ) -> List[EventSummary]:
        """Collect ladder-style crypto events across assets by scraping site search."""
        asset_filter = {asset.upper() for asset in assets} if assets else None
        summary_map: Dict[tuple, tuple[EventSummary, float]] = {}

        for code, config in ASSET_CONFIG.items():
            if asset_filter and code not in asset_filter:
                continue

            aliases = config["aliases"]
            tag_labels = config.get("tag_labels", [])
            seen_slugs: set[str] = set()
            aggregated_results: List[Dict[str, Any]] = []

            aggregated_results.extend(
                await self._collect_series_events(
                    config.get("series_slugs", []),
                    force_refresh=force_refresh
                )
            )

            if not aggregated_results:
                for term in config["queries"]:
                    base_params = {"query": term, "status": "active"}
                    aggregated_results.extend(
                        await self._fetch_search_results(base_params, force_refresh=force_refresh)
                    )

            for item in aggregated_results:
                slug = item.get("slug")
                if not slug or slug in seen_slugs:
                    continue

                if not self._matches_asset(item, aliases, tag_labels):
                    continue

                if not self._is_ladder_event(item, aliases):
                    continue

                volume_value = self._safe_float(item.get("volume")) or 0.0
                num_markets = len(item.get("markets") or [])
                summary = EventSummary(
                    title=item.get("title", slug),
                    slug=slug,
                    asset=code,
                    volume=self._format_volume(volume_value),
                    num_markets=num_markets,
                )

                key = (code, slug)
                current = summary_map.get(key)
                if not current or volume_value > current[1]:
                    summary_map[key] = (summary, volume_value)

                seen_slugs.add(slug)

        ordered_assets = ["BTC", "ETH", "SOL", "XRP"]

        def sort_key(entry: tuple[EventSummary, float]) -> tuple:
            summary, volume_value = entry
            asset_rank = ordered_assets.index(summary.asset) if summary.asset in ordered_assets else len(ordered_assets)
            return (asset_rank, -volume_value, summary.title.lower())

        sorted_entries = sorted(summary_map.values(), key=sort_key)
        return [entry[0] for entry in sorted_entries]


    async def _fetch_search_results(
        self,
        base_params: Dict[str, Any],
        force_refresh: bool = False,
        max_pages: int = 3
    ) -> List[Dict[str, Any]]:
        params = dict(base_params)
        headers = self._no_cache_headers() if force_refresh else None
        if force_refresh:
            params = {**params, "_ts": int(time.time())}

        try:
            response = await self.client.get(
                f"{self.base_url}/search",
                params=params,
                headers=headers
            )
            response.raise_for_status()
        except Exception as exc:
            print(f"Error fetching Polymarket search results: {exc}")
            return []

        soup = BeautifulSoup(response.text, 'lxml')
        next_data = self._load_next_data(soup)
        if not next_data:
            return []

        self._build_id = next_data.get("buildId") or self._build_id
        search_data = self._find_query_data(
            next_data,
            lambda key: isinstance(key, list) and key and key[0] == 'search'
        )
        if not isinstance(search_data, dict):
            return []

        results: List[Dict[str, Any]] = []
        pages = search_data.get('pages', [])
        for page in pages:
            results.extend(page.get('results', []) or [])

        if not pages:
            return results

        cursor = pages[-1].get('nextCursor')
        has_next = pages[-1].get('hasNextPage')
        page_count = 1
        base_json_params = dict(base_params)

        while has_next and cursor and page_count < max_pages:
            page_data = await self._fetch_search_page(base_json_params, cursor)
            if not page_data:
                break

            extra_pages = page_data.get('pages', [])
            if not extra_pages:
                break

            for page in extra_pages:
                results.extend(page.get('results', []) or [])

            cursor = extra_pages[-1].get('nextCursor')
            has_next = extra_pages[-1].get('hasNextPage')
            page_count += 1

        return results

    async def _fetch_search_page(
        self,
        base_params: Dict[str, Any],
        cursor: Any
    ) -> Optional[Dict[str, Any]]:
        if not self._build_id:
            return None

        params = dict(base_params)
        params['cursor'] = cursor

        try:
            response = await self.client.get(
                f"{self.base_url}/_next/data/{self._build_id}/search.json",
                params=params
            )
            response.raise_for_status()
        except Exception as exc:
            print(f"Error fetching Polymarket search cursor {cursor}: {exc}")
            return None

        data = response.json()
        return self._find_query_data(
            data,
            lambda key: isinstance(key, list) and key and key[0] == 'search'
        )

    def _matches_asset(
        self,
        item: Dict[str, Any],
        aliases: List[str],
        tag_labels: List[str]
    ) -> bool:
        alias_lower = [alias.lower() for alias in aliases]
        label_lower = [label.lower() for label in tag_labels]

        tags = [
            (tag.get('label') or '').lower()
            for tag in (item.get('tags') or [])
            if isinstance(tag, dict)
        ]
        if tags and any(label in tags for label in label_lower + alias_lower):
            return True

        texts = [
            item.get('title'),
            item.get('slug'),
            item.get('ticker'),
            item.get('seriesSlug'),
        ]

        for text in texts:
            if not text:
                continue
            normalized = text.replace('-', ' ').lower()
            if any(alias in normalized for alias in alias_lower):
                return True

        return False

    def _is_ladder_event(self, item: Dict[str, Any], aliases: List[str]) -> bool:
        series_slug = (item.get('seriesSlug') or '').lower()
        if 'multi-strikes' in series_slug:
            return True

        tags = [
            (tag.get('label') or '').lower()
            for tag in (item.get('tags') or [])
            if isinstance(tag, dict)
        ]
        if any('multi strikes' in tag for tag in tags):
            return True

        texts = [
            item.get('title') or '',
            (item.get('slug') or '').replace('-', ' '),
            (item.get('ticker') or '').replace('-', ' '),
            item.get('description') or '',
        ]

        for text in texts:
            if not text:
                continue
            lower = text.lower()
            for alias in aliases:
                alias_lower = alias.lower()
                if alias_lower not in lower:
                    continue
                if self._contains_ladder_keywords(lower, alias_lower):
                    return True

        return False

    def _contains_ladder_keywords(self, text_lower: str, alias_lower: str) -> bool:
        combos = [
            f'what price will {alias_lower}',
            f'{alias_lower} price on',
            f'price on {alias_lower}',
            f'{alias_lower} price be on',
            f'{alias_lower} price be at',
            f'{alias_lower} price will',
            f'price will {alias_lower}',
            f'{alias_lower} above',
            f'{alias_lower} price above',
        ]

        for combo in combos:
            if combo in text_lower:
                return True

        if alias_lower in text_lower and 'price' in text_lower:
            if any(keyword in text_lower for keyword in LADDER_KEYWORDS):
                return True

        if alias_lower in text_lower and any(month in text_lower for month in MONTH_KEYWORDS):
            if 'price' in text_lower or 'above' in text_lower:
                return True

        if alias_lower in text_lower and '___' in text_lower and 'above' in text_lower:
            return True

        return False

    async def _collect_series_events(
        self,
        series_slugs: List[str],
        force_refresh: bool = False
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for series_slug in series_slugs:
            event_slug = await self._resolve_series_event_slug(series_slug, force_refresh)
            if not event_slug:
                continue
            event = await self.parse_event_by_slug(event_slug, force_refresh=force_refresh)
            if not event:
                continue
            results.append(self._event_to_result_dict(event))
        return results

    async def _resolve_series_event_slug(
        self,
        series_slug: str,
        force_refresh: bool = False
    ) -> Optional[str]:
        url = f"{self.base_url}/series/{series_slug}"
        params = {"_ts": int(time.time())} if force_refresh else None
        headers = self._no_cache_headers() if force_refresh else None
        try:
            response = await self.client.get(url, params=params, headers=headers)
        except Exception as exc:
            print(f"Error resolving series {series_slug}: {exc}")
            return None

        if response.status_code == 404:
            return None

        try:
            response.raise_for_status()
        except Exception as exc:
            print(f"Error resolving series {series_slug}: {exc}")
            return None

        path = response.url.path
        if path.startswith("/event/"):
            return path.split("/event/")[-1]

        body = response.text.strip()
        if body.startswith('/event/'):
            return body.split('/event/')[-1]

        return None

    def _event_to_result_dict(self, event: Event) -> Dict[str, Any]:
        return {
            "title": event.title,
            "slug": event.slug,
            "markets": [{} for _ in event.markets],
            "volume": event.volume,
            "seriesSlug": event.series_slug,
            "tags": [{"label": tag} for tag in (event.tags or [])],
        }

    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()

    def _no_cache_headers(self) -> Dict[str, str]:
        return {
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Expires": "0",
        }

    def _load_next_data(self, soup: BeautifulSoup) -> Optional[Dict[str, Any]]:
        script = soup.find('script', {'id': '__NEXT_DATA__'})
        if not script or not script.string:
            return None
        try:
            return json.loads(script.string)
        except json.JSONDecodeError as exc:
            print(f"Failed to decode __NEXT_DATA__: {exc}")
            return None

    def _extract_queries(self, next_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        container: Dict[str, Any] = {}
        if isinstance(next_data, dict):
            if 'props' in next_data:
                container = next_data.get('props', {}).get('pageProps', {})
            else:
                container = next_data.get('pageProps', {})
        dehydrated = container.get('dehydratedState', {}) if isinstance(container, dict) else {}
        return dehydrated.get('queries', []) or []

    def _find_query_data(self, next_data: Dict[str, Any], matcher: Callable[[Any], bool]) -> Optional[Any]:
        for query in self._extract_queries(next_data):
            key = query.get('queryKey')
            try:
                if matcher(key):
                    return query.get('state', {}).get('data')
            except Exception:
                continue
        return None

    def _extract_event_data(self, next_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for query in self._extract_queries(next_data):
            key = query.get('queryKey')
            if key and '/api/event/slug' in str(key):
                data = query.get('state', {}).get('data')
                if isinstance(data, dict):
                    return data
        return None

    def _build_event_from_data(self, slug: str, event_data: Dict[str, Any]) -> Event:
        markets = self._parse_markets_from_json(event_data.get('markets', []))
        tags = [
            tag.get('label')
            for tag in (event_data.get('tags') or [])
            if isinstance(tag, dict) and tag.get('label')
        ]
        return Event(
            id=event_data.get('id', slug),
            title=event_data.get('title', slug),
            description=event_data.get('description', ''),
            slug=slug,
            markets=markets,
            resolve_time=event_data.get('endDate') or event_data.get('end_date'),
            tags=tags,
            series_slug=event_data.get('seriesSlug'),
            volume=self._safe_float(event_data.get('volume'))
        )

    def _gather_search_results(self, search_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for page in search_data.get('pages', []):
            results.extend(page.get('results', []) or [])
        return results

    def _detect_asset_code(
        self,
        item: Dict[str, Any],
        asset_filter: Optional[set[str]] = None
    ) -> Optional[str]:
        for code, config in ASSET_CONFIG.items():
            if asset_filter and code not in asset_filter:
                continue
            if self._matches_asset(item, config["aliases"], config.get("tag_labels", [])):
                return code
        return None

    def _safe_float(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _format_volume(self, volume: float) -> Optional[str]:
        if volume is None or volume <= 0:
            return None
        if volume >= 1_000_000:
            return f"${volume / 1_000_000:.1f}M"
        if volume >= 1_000:
            return f"${volume / 1_000:.1f}k"
        return f"${volume:,.0f}"
