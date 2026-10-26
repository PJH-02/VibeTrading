from decimal import Decimal

from core.models import AssetClass, Venue
from vibetrading.providers.coingecko import CoinGeckoClient, CoinMarket
from vibetrading.providers.universe import ListedContract, select_near_next_futures, select_option_atm_band


def test_select_near_next_futures() -> None:
    contracts = [
        ListedContract("F3", "F3", AssetClass.FUTURE, Venue.KIS, "2026-12-10", "202612"),
        ListedContract("F1", "F1", AssetClass.FUTURE, Venue.KIS, "2026-09-10", "202609"),
        ListedContract("F2", "F2", AssetClass.FUTURE, Venue.KIS, "2026-10-10", "202610"),
    ]
    assert [item.raw_symbol for item in select_near_next_futures(contracts, today="2026-01-01")] == ["F1", "F2"]


def test_select_option_atm_band_two_months() -> None:
    contracts = []
    for expiry, month in (("2026-09-10", "202609"), ("2026-10-10", "202610"), ("2026-11-10", "202611")):
        for strike in range(90, 112, 2):
            for right in ("C", "P"):
                contracts.append(
                    ListedContract(
                        f"{month}{right}{strike}",
                        f"{month}{right}{strike}",
                        AssetClass.OPTION,
                        Venue.KIS,
                        expiry,
                        month,
                        strike=Decimal(str(strike)),
                        option_right=right,
                    )
                )
    selected = select_option_atm_band(contracts, Decimal("100"), months=2, strikes_each_side=5, today="2026-01-01")
    assert len(selected) == 44
    assert {item.expiry for item in selected} == {"2026-09-10", "2026-10-10"}


def test_coingecko_excludes_usd_stables() -> None:
    assert CoinGeckoClient._excluded(CoinMarket("usds", "USDS", "USDS", 10)) is True
    assert CoinGeckoClient._excluded(CoinMarket("world-liberty-financial-usd", "USD1", "USD1", 11)) is True
