import httpx
from typing import Optional


class BinanceClient:
    def __init__(self, base_url: str = "https://data-api.binance.vision/api/v3"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(timeout=10.0)
    
    async def get_spot_price(self, symbol: str) -> Optional[float]:
        """
        Get spot price for a trading pair from Binance.
        Uses bookTicker endpoint to get bid/ask prices and calculates mid price.
        
        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT', 'ETHUSDT', 'SOLUSDT')
        
        Returns:
            Current mid spot price in USDT (bid + ask) / 2, or None if error
        """
        try:
            response = await self.client.get(
                f"{self.base_url}/ticker/bookTicker",
                params={"symbol": symbol}
            )
            response.raise_for_status()
            data = response.json()
            
            bid = float(data.get("bidPrice") or 0)
            ask = float(data.get("askPrice") or 0)
            
            if bid > 0 and ask > 0:
                mid = (bid + ask) / 2.0
                print(f"Binance spot for {symbol}: ${mid:.2f} (bid=${bid:.2f}, ask=${ask:.2f})")
                return mid
            else:
                print(f"Invalid bid/ask prices for {symbol}: bid={bid}, ask={ask}")
                return None
        except Exception as e:
            print(f"Error fetching Binance price for {symbol}: {e}")
            return None
    
    async def get_btc_price(self) -> Optional[float]:
        """Get BTC/USDT price"""
        return await self.get_spot_price("BTCUSDT")
    
    async def get_eth_price(self) -> Optional[float]:
        """Get ETH/USDT price"""
        return await self.get_spot_price("ETHUSDT")
    
    async def get_sol_price(self) -> Optional[float]:
        """Get SOL/USDT price"""
        return await self.get_spot_price("SOLUSDT")

    async def get_xrp_price(self) -> Optional[float]:
        """Get XRP/USDT price"""
        return await self.get_spot_price("XRPUSDT")

    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()
