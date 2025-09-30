import httpx
import re
from bs4 import BeautifulSoup
from typing import List, Optional, Dict, Any
from dataclasses import dataclass


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
    
    def extract_strike_from_text(self, text: str) -> Optional[StrikeMeta]:
        """
        Extract numerical strike from market question text.
        
        Examples:
            "$120k" -> StrikeMeta(raw="$120k", K=120000, unit="USD")
            "100,000" -> StrikeMeta(raw="100,000", K=100000, unit="USD")
            "$4.5M" -> StrikeMeta(raw="$4.5M", K=4500000, unit="USD")
        """
        patterns = [
            r'\$?\s?(\d+[\d,]*\.?\d*)\s?(k|K)(?!\w)',
            r'\$?\s?(\d+[\d,]*\.?\d*)\s?(m|M)(?!\w)',
            r'\$\s?(\d+[\d,]*)',
            r'(\d+[\d,]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
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
    
    async def parse_event_by_slug(self, slug: str) -> Optional[Event]:
        """
        Parse Polymarket event page by slug.
        
        Extracts data from Next.js __NEXT_DATA__ JSON embedded in HTML.
        """
        url = f"{self.base_url}/event/{slug}"
        
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'lxml')
            
            # Try to extract from __NEXT_DATA__ (Next.js embedded JSON)
            next_data_script = soup.find('script', {'id': '__NEXT_DATA__'})
            if next_data_script and next_data_script.string:
                import json
                next_data = json.loads(next_data_script.string)
                
                # Navigate to event data in dehydratedState
                queries = next_data.get('props', {}).get('pageProps', {}).get('dehydratedState', {}).get('queries', [])
                
                for query in queries:
                    if '/api/event/slug' in str(query.get('queryKey', [])):
                        event_data = query.get('state', {}).get('data', {})
                        
                        if event_data:
                            markets = self._parse_markets_from_json(event_data.get('markets', []))

                            return Event(
                                id=event_data.get('id', slug),
                                title=event_data.get('title', slug),
                                description=event_data.get('description', ''),
                                slug=slug,
                                markets=markets,
                                resolve_time=event_data.get('endDate') or event_data.get('end_date')
                            )
            
            # Fallback to HTML parsing
            title = self._extract_title(soup)
            description = self._extract_description(soup)
            markets = self._extract_markets(soup)
            
            event = Event(
                id=slug,
                title=title or slug,
                description=description or "",
                slug=slug,
                markets=markets
            )
            
            return event
            
        except Exception as e:
            print(f"Error parsing Polymarket event {slug}: {e}")
            return None
    
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
        """
        Parse markets from JSON data (from __NEXT_DATA__).
        """
        markets = []
        
        for market_data in markets_data:
            question = market_data.get('question', '')
            strike = self.extract_strike_from_text(question)

            # Extract YES/NO prices from outcomePrices
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

            # Skip markets that are closed or not accepting orders to mirror live Polymarket view
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
        """
        Extract markets from event page HTML (fallback method).
        
        This is a simplified parser for MVP.
        We'll look for common patterns in Polymarket's HTML structure.
        """
        markets = []
        
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
    
    async def get_crypto_events(self, assets: List[str] = ["BTC", "ETH", "SOL"]) -> List[EventSummary]:
        """
        Get list of crypto ladder events for given assets.
        
        For MVP, returns a curated list of known events from Polymarket's crypto category.
        TODO: Implement dynamic parsing from /crypto page in future version.
        """
        # Hardcoded list of known crypto ladder events (updated regularly)
        known_events = [
            # Bitcoin events
            EventSummary(title="What price will Bitcoin hit September 29-October 5?", slug="what-price-will-bitcoin-hit-september-29-october-5", asset="BTC", volume="$8.7k", num_markets=15),
            EventSummary(title="Bitcoin above ___ on September 30?", slug="bitcoin-above-on-september-30", asset="BTC", volume="$2.3k", num_markets=11),
            EventSummary(title="Bitcoin price on September 30?", slug="bitcoin-price-on-september-30", asset="BTC", volume="$1.5k", num_markets=11),
            EventSummary(title="What price will Bitcoin hit in September?", slug="what-price-will-bitcoin-hit-in-september", asset="BTC", volume="$0", num_markets=15),
            
            # Ethereum events  
            EventSummary(title="What price will Ethereum hit September 29-October 5?", slug="what-price-will-ethereum-hit-september-29-october-5", asset="ETH", volume="$8.7k", num_markets=15),
            EventSummary(title="Ethereum above ___ on September 30?", slug="ethereum-above-on-september-30", asset="ETH", volume="$1.8k", num_markets=11),
            EventSummary(title="Ethereum price on September 30?", slug="ethereum-price-on-september-30", asset="ETH", volume="$0.9k", num_markets=11),
            EventSummary(title="What price will Ethereum hit in September?", slug="what-price-will-ethereum-hit-in-september", asset="ETH", volume="$0", num_markets=15),
            
            # Solana events
            EventSummary(title="What price will Solana hit September 29-October 5?", slug="what-price-will-solana-hit-september-29-october-5", asset="SOL", volume="$4.2k", num_markets=13),
            EventSummary(title="Solana above ___ on September 30?", slug="solana-above-on-september-30", asset="SOL", volume="$1.1k", num_markets=10),
            EventSummary(title="What price will Solana hit in September?", slug="what-price-will-solana-hit-in-september", asset="SOL", volume="$0", num_markets=13),
        ]
        
        # Filter by requested assets
        filtered = [e for e in known_events if e.asset in assets]
        
        print(f"Returning {len(filtered)} known crypto events for assets: {assets}")
        return filtered
    
    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()
