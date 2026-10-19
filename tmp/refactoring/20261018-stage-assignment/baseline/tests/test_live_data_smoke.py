import pytest

from vibetrading.providers.binance import BinanceCatalog
from vibetrading.providers.coingecko import CoinGeckoClient
from vibetrading.providers.hyperliquid import HyperliquidCatalog


pytestmark = pytest.mark.live_data


@pytest.mark.asyncio
async def test_coingecko_binance_top_pairs_live() -> None:
    coins = await CoinGeckoClient().top_market_cap_symbols(limit=5)
    assert coins
    pairs = await BinanceCatalog().top_usdt_pairs(limit=5)
    assert pairs
    assert all(pair.endswith("USDT") for pair in pairs)


@pytest.mark.asyncio
async def test_hyperliquid_assets_live() -> None:
    assets = await HyperliquidCatalog().assets()
    assert assets
    assert any(asset.asset_class == "crypto_perp" for asset in assets)
