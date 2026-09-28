from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Iterable

from vibetrading.models import AssetClass, InstrumentId, Venue, stable_hash
from vibetrading.providers.hyperliquid import HyperliquidCatalog, load_hyperliquid_tokenized_symbols
from vibetrading.universe_config import configured_symbols as load_configured_symbols, load_universe_config
from vibetrading.settings import RuntimeSettings, get_settings
from vibetrading.sqlite_store import RuntimeStore


INDEX_PROXY_ETFS: dict[str, InstrumentId] = {
    "KOSPI200": InstrumentId(
        venue=Venue.TOSS,
        asset_class=AssetClass.ETF,
        raw_symbol="069500",
        std_symbol="069500",
        underlying="KOSPI200",
        currency="KRW",
        exchange="KRX",
    ),
    "KOSDAQ150": InstrumentId(
        venue=Venue.TOSS,
        asset_class=AssetClass.ETF,
        raw_symbol="229200",
        std_symbol="229200",
        underlying="KOSDAQ150",
        currency="KRW",
        exchange="KRX",
    ),
    "NASDAQ": InstrumentId(
        venue=Venue.TOSS,
        asset_class=AssetClass.ETF,
        raw_symbol="SPY",
        std_symbol="SPY",
        underlying="NASDAQ",
        currency="USD",
        exchange="NYSEARCA",
    ),
}

KST = timezone(timedelta(hours=9))


def _unexpired(expiry: str, today: str | None = None) -> bool:
    """Reject stale master rows before front/next or option selection."""
    current = today or datetime.now(KST).strftime("%Y%m%d")
    normalized = str(expiry or "").replace("-", "")
    if len(normalized) >= 8 and normalized[:8].isdigit():
        return normalized[:8] >= current
    # KRX/KIS masters often expose only YYYYMM. Keep the current month—the
    # session/status resolver performs the exact expiry-day rollover—and
    # reject every earlier month.
    if len(normalized) >= 6 and normalized[:6].isdigit():
        return normalized[:6] >= current[:6]
    return False


@dataclass(frozen=True)
class ListedContract:
    raw_symbol: str
    std_symbol: str
    asset_class: AssetClass
    venue: Venue
    expiry: str
    contract_month: str
    strike: Decimal | None = None
    option_right: str | None = None
    tradable: bool = True
    underlying: str | None = None
    atm_flag: str | None = None
    currency: str = "KRW"
    exchange: str = "KRX"

    def instrument(self) -> InstrumentId:
        return InstrumentId(
            venue=self.venue,
            asset_class=self.asset_class,
            raw_symbol=self.raw_symbol,
            std_symbol=self.std_symbol,
            contract_month=self.contract_month,
            expiry=self.expiry,
            strike=self.strike,
            option_right=self.option_right,
            underlying=self.underlying,
            currency=self.currency,
            exchange=self.exchange,
        )


def select_near_next_futures(contracts: Iterable[ListedContract], *, today: str | None = None) -> list[ListedContract]:
    futures = [
        item for item in contracts
        if item.asset_class == AssetClass.FUTURE and item.tradable and _unexpired(item.expiry, today)
    ]
    return sorted(futures, key=lambda item: (item.expiry, item.raw_symbol))[:2]


def select_option_atm_band(
    contracts: Iterable[ListedContract],
    underlying_price: Decimal | None,
    months: int = 2,
    strikes_each_side: int = 1,
    today: str | None = None,
) -> list[ListedContract]:
    options = [
        item for item in contracts
        if item.asset_class == AssetClass.OPTION
        and item.tradable
        and item.strike is not None
        and _unexpired(item.expiry, today)
    ]
    expiries = sorted({item.expiry for item in options})[:months]
    selected: list[ListedContract] = []
    for expiry in expiries:
        expiry_options = [item for item in options if item.expiry == expiry]
        strikes = sorted({item.strike for item in expiry_options if item.strike is not None})
        if not strikes:
            continue
        if underlying_price is None:
            master_atm = sorted(
                {item.strike for item in expiry_options if item.strike is not None and item.atm_flag == "1"}
            )
            atm = master_atm[0] if master_atm else strikes[len(strikes) // 2]
        else:
            atm = min(strikes, key=lambda strike: abs(strike - underlying_price))
        atm_index = strikes.index(atm)
        chosen_strikes = set(strikes[max(0, atm_index - strikes_each_side) : atm_index + strikes_each_side + 1])
        selected.extend(
            sorted(
                [item for item in expiry_options if item.strike in chosen_strikes],
                key=lambda item: (item.expiry, item.strike or Decimal("0"), item.option_right or "", item.raw_symbol),
            )
        )
    return selected


class InstrumentUniverseResolver:
    def __init__(self, store: RuntimeStore | None = None, settings: RuntimeSettings | None = None) -> None:
        self.settings = settings or get_settings()
        # Loading a manually configured universe is part of every collector
        # reconnect.  It must remain a filesystem-only operation: opening or
        # writing the shared SQLite catalogue here can block the collectors'
        # event loop behind storage or maintenance work.
        self.store = store

    def configured_symbols(self, provider: str) -> list[str]:
        """Return manually managed symbols for a provider from the unified policy."""
        return load_configured_symbols(provider)

    def configured_policy(self) -> dict:
        policy = load_universe_config()
        self._persist("universe_policy", policy)
        return policy

    def resolve_index_proxy_etfs(self) -> dict[str, InstrumentId]:
        self._persist(
            "index_proxy_etfs",
            {name: instrument.model_dump(mode="json") for name, instrument in INDEX_PROXY_ETFS.items()},
        )
        return dict(INDEX_PROXY_ETFS)

    async def resolve_hyperliquid_assets(self) -> list[dict]:
        assets = await HyperliquidCatalog().assets()
        tokenized = self.configured_symbols("hyperliquid") or load_hyperliquid_tokenized_symbols(
            self.settings.external_config_dir
        )
        payload = {
            "assets": [asset.__dict__ for asset in assets],
            "configured_tokenized_equities": tokenized,
        }
        self._persist("hyperliquid_assets", payload)
        return payload["assets"]

    def resolve_hyperliquid_tokenized_equities(self) -> list[str]:
        symbols = self.configured_symbols("hyperliquid") or load_hyperliquid_tokenized_symbols(
            self.settings.external_config_dir
        )
        self._persist("hyperliquid_tokenized_equities", symbols)
        return symbols

    def resolve_derivatives_from_master(
        self,
        contracts: Iterable[ListedContract],
        underlying_price: Decimal | None,
        strikes_each_side: int = 1,
    ) -> dict[str, list[InstrumentId]]:
        near_next = select_near_next_futures(contracts)
        options = select_option_atm_band(contracts, underlying_price, strikes_each_side=strikes_each_side)
        payload = {
            "futures": [item.instrument().model_dump(mode="json") for item in near_next],
            "options": [item.instrument().model_dump(mode="json") for item in options],
        }
        self._persist("kr_derivatives_dynamic", payload)
        return {
            "futures": [item.instrument() for item in near_next],
            "options": [item.instrument() for item in options],
        }

    async def resolve_kis_index_derivatives(
        self,
        *,
        underlying: str = "KOSPI200",
        underlying_price: Decimal | None = None,
        strikes_each_side: int = 1,
    ) -> dict[str, list[InstrumentId]]:
        from vibetrading.providers.kis_master import KisMasterDownloader, filter_underlying

        contracts = await KisMasterDownloader().index_derivatives()
        selected_contracts = filter_underlying(contracts, underlying)
        result = self.resolve_derivatives_from_master(selected_contracts, underlying_price, strikes_each_side=strikes_each_side)
        self._persist(
            f"kis_index_derivatives_{underlying.lower()}",
            {
                "underlying": underlying,
                "underlying_price": str(underlying_price) if underlying_price is not None else None,
                "futures": [item.model_dump(mode="json") for item in result["futures"]],
                "options": [item.model_dump(mode="json") for item in result["options"]],
            },
        )
        return result

    async def resolve_kis_commodity_futures(
        self,
        *,
        products: Iterable[str] = ("B10", "USD"),
    ) -> dict[str, list[InstrumentId]]:
        """Resolve KIS domestic commodity front/next contracts.

        The master is refreshed by the caller at boot/pre-open/rollover.  The
        selection itself is deterministic and never embeds a contract code.
        """
        from vibetrading.providers.kis_master import KisMasterDownloader

        contracts = await KisMasterDownloader().commodity_futures(products)
        selected: dict[str, list[InstrumentId]] = {}
        for product in {str(item).strip().upper() for item in products if str(item).strip()}:
            candidates = [
                item for item in contracts
                if (item.underlying or "").upper() == product and item.tradable
            ]
            chosen = select_near_next_futures(candidates)
            selected[product] = [item.instrument() for item in chosen]
        payload = {
            product: [item.model_dump(mode="json") for item in instruments]
            for product, instruments in selected.items()
        }
        self._persist("kis_commodity_futures", payload)
        return selected

    async def resolve_kis_overseas_futures(
        self,
        *,
        products: Iterable[str] = ("NQ",),
    ) -> dict[str, list[InstrumentId]]:
        """Resolve KIS overseas futures front/next contracts from ffcode.mst."""
        from vibetrading.providers.kis_master import KisMasterDownloader

        contracts = await KisMasterDownloader().overseas_futures(products)
        selected: dict[str, list[InstrumentId]] = {}
        for product in {str(item).strip().upper() for item in products if str(item).strip()}:
            candidates = [
                item for item in contracts
                if (item.underlying or "").upper() == product and item.tradable
            ]
            chosen = select_near_next_futures(candidates)
            selected[product] = [item.instrument() for item in chosen]
        payload = {
            product: [item.model_dump(mode="json") for item in instruments]
            for product, instruments in selected.items()
        }
        self._persist("kis_overseas_futures", payload)
        return selected

    def _persist(self, universe_id: str, payload: object) -> None:
        store = self.store
        owned_store = store is None
        if store is None:
            store = RuntimeStore(self.settings)
        try:
            store.write_universe(
                universe_id=stable_hash([universe_id, payload]),
                provider=universe_id,
                payload={"items": payload},
                valid_from_ts_ns=time.time_ns(),
            )
        finally:
            if owned_store:
                store.close()
