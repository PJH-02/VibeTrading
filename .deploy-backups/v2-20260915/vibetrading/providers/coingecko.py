from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiohttp


STABLE_SYMBOLS = {
    "usdt",
    "usdc",
    "dai",
    "tusd",
    "usde",
    "fdusd",
    "busd",
    "usdp",
    "frax",
    "gusd",
    "lusd",
    "pyusd",
    "usdd",
    "usds",
    "usd1",
    "usdx",
    "usdy",
}
WRAPPED_HINTS = ("wrapped", "bridged", "staked", "liquid staking")
STABLE_HINTS = ("stablecoin", "usd stable", "dollar")


@dataclass(frozen=True)
class CoinMarket:
    id: str
    symbol: str
    name: str
    market_cap_rank: int


class CoinGeckoClient:
    base_url = "https://api.coingecko.com/api/v3"

    async def top_market_cap_symbols(self, limit: int = 20) -> list[CoinMarket]:
        per_page = max(50, limit * 3)
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": str(per_page),
            "page": "1",
            "sparkline": "false",
        }
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{self.base_url}/coins/markets", params=params) as response:
                response.raise_for_status()
                payload = await response.json()
        coins = [self._parse(row) for row in payload]
        filtered = [coin for coin in coins if not self._excluded(coin)]
        return filtered[:limit]

    @staticmethod
    def _parse(row: dict[str, Any]) -> CoinMarket:
        return CoinMarket(
            id=str(row.get("id") or ""),
            symbol=str(row.get("symbol") or "").upper(),
            name=str(row.get("name") or ""),
            market_cap_rank=int(row.get("market_cap_rank") or 0),
        )

    @staticmethod
    def _excluded(coin: CoinMarket) -> bool:
        symbol = coin.symbol.lower()
        name = coin.name.lower()
        coin_id = coin.id.lower()
        if symbol in STABLE_SYMBOLS:
            return True
        if symbol.startswith("usd") or symbol.endswith("usd"):
            return True
        if any(hint in name or hint in coin_id for hint in STABLE_HINTS):
            return True
        return any(hint in name or hint in coin_id for hint in WRAPPED_HINTS)
