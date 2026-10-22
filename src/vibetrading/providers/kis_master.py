from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from typing import Iterable
from zipfile import ZipFile

import aiohttp

from vibetrading.models import AssetClass, Venue
from vibetrading.providers.universe import ListedContract


INDEX_DERIVATIVE_MASTER_URL = "https://new.real.download.dws.co.kr/common/master/fo_idx_code_mts.mst.zip"
COMMODITY_FUTURE_MASTER_URL = "https://new.real.download.dws.co.kr/common/master/fo_com_code.mst.zip"
OVERSEAS_FUTURE_MASTER_URL = "https://new.real.download.dws.co.kr/common/master/ffcode.mst.zip"
COMMODITY_PRODUCT_ALIASES: dict[str, set[str]] = {
    # KIS has used B10 in documentation and BMA in current master rows for
    # the same 10-year KTB contract.
    "B10": {"B10", "BMA"},
    "USD": {"USD"},
}
OVERSEAS_FUTURE_PRODUCT_ALIASES: dict[str, set[str]] = {
    "NQ": {"NQ"},
    "MNQ": {"MNQ"},
}
REGULAR_INDEX_FUTURE_PRODUCT_KINDS = {"1", "3"}
REGULAR_KOSPI200_OPTION_PRODUCT_KINDS = {"5", "6"}


@dataclass(frozen=True)
class KisIndexDerivativeMasterRow:
    product_kind: str
    raw_symbol: str
    standard_symbol: str
    korean_name: str
    atm_flag: str
    strike: Decimal | None
    month_rank: str
    underlying_code: str
    underlying_name: str


@dataclass(frozen=True)
class KisCommodityFutureMasterRow:
    commodity_type: str
    info_type: str
    raw_symbol: str
    standard_symbol: str
    korean_name: str
    month_rank: str
    product_code: str
    product_name: str


@dataclass(frozen=True)
class KisOverseasFutureMasterRow:
    raw_symbol: str
    korean_name: str
    exchange: str
    product_code: str
    product_kind: str
    display_decimal: str
    calc_decimal: str
    tick_size: str
    tick_value: str
    contract_size: str
    most_active_flag: str
    front_month_flag: str
    spread_flag: str
    sub_exchange: str


def parse_index_derivative_master(text: str) -> list[ListedContract]:
    contracts: list[ListedContract] = []
    for row in _parse_rows(text):
        contract_month = _contract_month(row.korean_name)
        if not contract_month:
            continue
        asset_class = _asset_class(row)
        if asset_class is None:
            continue
        option_right = _option_right(row.korean_name) if asset_class == AssetClass.OPTION else None
        if asset_class == AssetClass.OPTION and option_right is None:
            continue
        expiry = f"{contract_month[:4]}-{contract_month[4:]}"
        contracts.append(
            ListedContract(
                raw_symbol=row.raw_symbol,
                std_symbol=row.standard_symbol or row.raw_symbol,
                asset_class=asset_class,
                venue=Venue.KIS,
                expiry=expiry,
                contract_month=contract_month,
                strike=row.strike if asset_class == AssetClass.OPTION else None,
                option_right=option_right,
                tradable=_is_current_or_future_month(contract_month),
                underlying=row.underlying_name,
                atm_flag=row.atm_flag or None,
            )
        )
    return contracts


class KisMasterDownloader:
    async def index_derivatives(self) -> list[ListedContract]:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(INDEX_DERIVATIVE_MASTER_URL) as response:
                response.raise_for_status()
                payload = await response.read()
        return parse_index_derivative_master(_read_single_mst_from_zip(payload))

    async def commodity_futures(self, products: Iterable[str] = ("B10", "USD")) -> list[ListedContract]:
        """Download the current KIS domestic commodity-futures master.

        The file is a fixed-width CP949 file.  Product definitions are stable,
        but contract rows and their near/next-month rank are refreshed by KIS,
        so callers should invoke this around rollovers rather than hard-code
        contract symbols.
        """
        wanted = {str(item).strip().upper() for item in products if str(item).strip()}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(COMMODITY_FUTURE_MASTER_URL) as response:
                response.raise_for_status()
                payload = await response.read()
        rows = parse_commodity_future_master(_read_single_mst_from_zip(payload))
        accepted = {
            code
            for product in wanted
            for code in COMMODITY_PRODUCT_ALIASES.get(product, {product})
        }
        return [contract for contract in rows if contract.underlying and contract.underlying.upper() in accepted]

    async def overseas_futures(self, products: Iterable[str] = ("NQ",)) -> list[ListedContract]:
        """Download KIS overseas futures master and filter by product code."""
        wanted = {str(item).strip().upper() for item in products if str(item).strip()}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(OVERSEAS_FUTURE_MASTER_URL) as response:
                response.raise_for_status()
                payload = await response.read()
        rows = parse_overseas_future_master(_read_single_mst_from_zip(payload))
        accepted = {
            code
            for product in wanted
            for code in OVERSEAS_FUTURE_PRODUCT_ALIASES.get(product, {product})
        }
        return [contract for contract in rows if contract.underlying and contract.underlying.upper() in accepted]


def parse_commodity_future_master(text: str) -> list[ListedContract]:
    """Parse ``fo_com_code.mst`` rows into the shared contract model.

    KIS' published layout is 55 characters of the first record followed by a
    second fixed-width record.  We intentionally keep only outright futures
    (info_type ``1``), excluding SP futures and options.
    """
    contracts: list[ListedContract] = []
    for row in _parse_commodity_rows(text):
        if row.info_type != "1" or row.korean_name.upper().lstrip().startswith("SP"):
            continue
        contract_month = _contract_month(row.korean_name)
        if not contract_month:
            continue
        contracts.append(
            ListedContract(
                raw_symbol=row.raw_symbol,
                std_symbol=row.standard_symbol or row.raw_symbol,
                asset_class=AssetClass.FUTURE,
                venue=Venue.KIS,
                expiry=f"{contract_month[:4]}-{contract_month[4:]}",
                contract_month=contract_month,
                tradable=_is_current_or_future_month(contract_month),
                underlying=_canonical_commodity_product(row.product_code, row.product_name),
            )
        )
    return contracts


def parse_overseas_future_master(text: str) -> list[ListedContract]:
    """Parse KIS ``ffcode.mst`` rows into outright overseas futures.

    The official KIS sample uses negative offsets for the stable tail fields.
    We follow that layout and avoid deriving synthetic contracts.
    """
    contracts: list[ListedContract] = []
    for row in _parse_overseas_future_rows(text):
        if row.spread_flag == "Y" or "-" in row.raw_symbol or not row.product_code:
            continue
        contract_month = _contract_month(row.korean_name)
        if not contract_month:
            continue
        contracts.append(
            ListedContract(
                raw_symbol=row.raw_symbol,
                std_symbol=row.raw_symbol,
                asset_class=AssetClass.FUTURE,
                venue=Venue.KIS,
                expiry=f"{contract_month[:4]}-{contract_month[4:]}",
                contract_month=contract_month,
                tradable=_is_current_or_future_month(contract_month),
                underlying=row.product_code,
                currency="USD",
                exchange=row.exchange or row.sub_exchange or "UNKNOWN",
            )
        )
    return contracts


def filter_underlying(contracts: Iterable[ListedContract], underlying: str) -> list[ListedContract]:
    wanted = underlying.strip().upper()
    return [item for item in contracts if (item.underlying or "").strip().upper() == wanted]


def _read_single_mst_from_zip(payload: bytes) -> str:
    with ZipFile(BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".mst")]
        if not names:
            raise RuntimeError("KIS index derivative master zip did not contain an .mst file")
        return archive.read(names[0]).decode("cp949", errors="replace")


def _parse_commodity_rows(text: str) -> list[KisCommodityFutureMasterRow]:
    rows: list[KisCommodityFutureMasterRow] = []
    for line in text.splitlines():
        if len(line) < 55:
            continue
        first = line[:55]
        second = line[55:].lstrip()
        if len(second) < 12:
            continue
        rows.append(
            KisCommodityFutureMasterRow(
                commodity_type=first[0:1].strip(),
                info_type=first[1:2].strip(),
                raw_symbol=first[2:11].strip(),
                standard_symbol=first[11:23].strip(),
                korean_name=first[23:55].strip(),
                month_rank=second[8:9].strip(),
                product_code=second[9:12].strip().upper(),
                product_name=second[12:].strip(),
            )
        )
    return rows


def _parse_overseas_future_rows(text: str) -> list[KisOverseasFutureMasterRow]:
    rows: list[KisOverseasFutureMasterRow] = []
    for line in text.splitlines():
        if len(line) < 199:
            continue
        rows.append(
            KisOverseasFutureMasterRow(
                raw_symbol=line[:32].strip(),
                korean_name=line[82:107].strip(),
                exchange=line[-92:-82].strip(),
                product_code=line[-82:-72].strip().upper(),
                product_kind=line[-72:-69].strip(),
                display_decimal=line[-69:-64].strip(),
                calc_decimal=line[-64:-59].strip(),
                tick_size=line[-59:-45].strip(),
                tick_value=line[-45:-31].strip(),
                contract_size=line[-31:-21].strip(),
                most_active_flag=line[-7:-6].strip(),
                front_month_flag=line[-6:-5].strip(),
                spread_flag=line[-5:-4].strip(),
                sub_exchange=line[-3:].strip(),
            )
        )
    return rows


def _canonical_commodity_product(product_code: str, product_name: str) -> str:
    code = product_code.strip().upper()
    for canonical, aliases in COMMODITY_PRODUCT_ALIASES.items():
        if code in aliases:
            return canonical
    # Keep unknown products visible instead of silently guessing.
    return code or product_name.strip().upper()


def _parse_rows(text: str) -> list[KisIndexDerivativeMasterRow]:
    rows: list[KisIndexDerivativeMasterRow] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split("|")
        if len(fields) < 9:
            continue
        rows.append(
            KisIndexDerivativeMasterRow(
                product_kind=fields[0].strip(),
                raw_symbol=fields[1].strip(),
                standard_symbol=fields[2].strip(),
                korean_name=fields[3].strip(),
                atm_flag=fields[4].strip(),
                strike=_parse_strike(fields[5]),
                month_rank=fields[6].strip(),
                underlying_code=fields[7].strip(),
                underlying_name=fields[8].strip().upper(),
            )
        )
    return rows


def _asset_class(row: KisIndexDerivativeMasterRow) -> AssetClass | None:
    if row.strike is not None and row.strike > 0:
        if row.product_kind in REGULAR_KOSPI200_OPTION_PRODUCT_KINDS:
            return AssetClass.OPTION
        return None
    name = row.korean_name.upper()
    if "SP" in name:
        return None
    if row.product_kind in REGULAR_INDEX_FUTURE_PRODUCT_KINDS and "F" in name:
        return AssetClass.FUTURE
    return None


def _contract_month(name: str) -> str | None:
    match = re.search(r"(20\d{4})", name)
    return match.group(1) if match else None


def _option_right(name: str) -> str | None:
    compact = re.sub(r"\s+", "", name.upper())
    if re.search(r"C20\d{4}", compact):
        return "CALL"
    if re.search(r"P20\d{4}", compact):
        return "PUT"
    return None


def _parse_strike(value: str) -> Decimal | None:
    clean = value.replace(",", "").strip()
    if not clean:
        return None
    strike = Decimal(clean)
    return strike if strike != 0 else None


def _is_current_or_future_month(contract_month: str) -> bool:
    # KIS master files usually only contain active/current and future contracts.
    # Keep a lightweight guard here so stale fixtures do not become tradable.
    from datetime import datetime

    return contract_month >= datetime.now().strftime("%Y%m")

