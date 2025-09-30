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


@dataclass
class Event:
    id: str
    title: str
    description: str
    slug: str
    markets: List[Market]
    resolve_time: Optional[str] = None


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
        
        For MVP: This is a basic HTML parser.
        In production, we'll use official Polymarket API.
        """
        url = f"{self.base_url}/event/{slug}"
        
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'lxml')
            
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
    
    def _extract_markets(self, soup: BeautifulSoup) -> List[Market]:
        """
        Extract markets from event page.
        
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
    
    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()
