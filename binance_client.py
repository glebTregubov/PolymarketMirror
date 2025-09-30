import httpx
from typing import Optional


class BinanceClient:
    def __init__(self, base_url: str = "https://api.binance.com/api/v3"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(timeout=10.0)
    
    async def get_spot_price(self, symbol: str) -> Optional[float]:
        """
        Get spot price for a trading pair from Binance.
        
        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT', 'ETHUSDT', 'SOLUSDT')
        
        Returns:
            Current spot price in USDT, or None if error
        """
        try:
            response = await self.client.get(
                f"{self.base_url}/ticker/price",
                params={"symbol": symbol}
            )
            response.raise_for_status()
            data = response.json()
            return float(data["price"])
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
    
    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()
