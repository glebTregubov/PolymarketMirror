import os
from typing import Any, Dict, List, Optional

import httpx


DEFAULT_DATA_API_BASE = "https://data-api.polymarket.com"
DEFAULT_CLOB_API_BASE = "https://clob.polymarket.com"
DEFAULT_POLYGON_RPC_URL = "https://polygon-rpc.com"
USDC_CONTRACT_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDC_DECIMALS = 6


class PolymarketOrdersClient:
    """
    Lightweight client focused on reading user-centric state such as positions and trades.
    """

    def __init__(
        self,
        data_api_base: str = DEFAULT_DATA_API_BASE,
        clob_api_base: str = DEFAULT_CLOB_API_BASE,
        private_key: Optional[str] = None,
        polygon_rpc_url: Optional[str] = None,
        usdc_contract: str = USDC_CONTRACT_ADDRESS,
        timeout: float = 20.0,
    ) -> None:
        self._data_api_base = data_api_base.rstrip("/")
        self._clob_api_base = clob_api_base.rstrip("/")
        self._private_key = private_key or os.getenv("POLYMARKET_PRIVATE_KEY")
        self._polygon_rpc_url = polygon_rpc_url or os.getenv("POLYGON_RPC_URL", DEFAULT_POLYGON_RPC_URL)
        self._usdc_contract = usdc_contract
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_positions(
        self,
        user_address: str,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Return open positions for a user address."""
        endpoint = f"{self._data_api_base}/positions"
        params = {
            "user": user_address,
            "limit": limit,
            "offset": offset,
        }
        response = await self._client.get(endpoint, params=params)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []

    async def get_usdc_balance(self, user_address: str) -> float:
        """Return Polygon USDC balance for a wallet."""
        if not self._polygon_rpc_url or not user_address:
            return 0.0
        address = user_address.strip()
        if not address.startswith("0x") or len(address) != 42:
            return 0.0

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": [
                {
                    "to": self._usdc_contract,
                    "data": "0x70a08231" + address.lower().replace("0x", "").rjust(64, "0")
                },
                "latest"
            ],
        }

        try:
            response = await self._client.post(self._polygon_rpc_url, json=payload)
            response.raise_for_status()
            data: Dict[str, Any] = response.json()
            raw_result = data.get("result")
            if not isinstance(raw_result, str):
                return 0.0
            balance_int = int(raw_result, 16)
            return balance_int / (10 ** USDC_DECIMALS)
        except Exception:
            return 0.0

    async def get_trades(
        self,
        user_address: str,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Return trade history (maker or taker) for a user address."""
        endpoint = f"{self._data_api_base}/trades"
        params = {
            "user": user_address,
            "limit": limit,
            "offset": offset,
        }
        response = await self._client.get(endpoint, params=params)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []
