from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import websockets

from runtime.bus import EventBus
from core.models import AssetClass, BookUpdateType, InstrumentId, IndexQuoteEvent, OrderBookEvent, OrderBookLevel, ParticipantFlowEvent, Side, TradeEvent, Venue, utc_now_ns
from feeds.providers.kis_client import KisAuthClient


logger = logging.getLogger(__name__)
_KIS_SUBSCRIPTION_LOCK = asyncio.Lock()
_KIS_LAST_SUBSCRIPTION_SEND = 0.0


async def _send_kis_subscription_frame(ws: Any, payload: dict[str, Any], *, use_mock: bool) -> None:
    """Serialize registration traffic across every KIS socket in-process."""
    global _KIS_LAST_SUBSCRIPTION_SEND
    interval = 0.5 if use_mock else 0.15
    async with _KIS_SUBSCRIPTION_LOCK:
        now = asyncio.get_running_loop().time()
        remaining = interval - (now - _KIS_LAST_SUBSCRIPTION_SEND)
        if remaining > 0:
            await asyncio.sleep(remaining)
        await ws.send(json.dumps(payload, ensure_ascii=False))
        _KIS_LAST_SUBSCRIPTION_SEND = asyncio.get_running_loop().time()
KST = timezone(timedelta(hours=9))


# KIS' market-level snapshot has a fixed set of participant columns.  Keep the
# provider prefixes intact in storage while supplying stable readable labels.
_KIS_PARTICIPANT_GROUPS: tuple[tuple[str, str], ...] = (
    ("prsn", "individual"),
    ("frgn", "foreign"),
    ("orgn", "institution_total"),
    ("ivtr", "financial_investment"),
    ("insu", "insurance"),
    ("invtr", "investment_trust"),
    ("bank", "bank"),
    ("mrbn", "merchant_bank"),
    ("fund", "other_finance"),
    ("pe_fund", "private_equity_fund"),
    ("etc_orgt", "other_institution"),
    ("scrt", "securities"),
    ("etc_corp", "other_corporation"),
)


def _flow_decimal(row: dict[str, Any], key: str) -> Decimal:
    """KIS uses empty strings for unavailable numeric values."""
    value = row.get(key)
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value).replace(",", ""))


def kis_participant_flow_events(
    rows: list[dict[str, Any]],
    *,
    market_code: str,
    market_subcode: str,
    wallclock_ts_ns: int,
    collector_run_id: str,
    collection_start_ts_ns: int,
) -> list[ParticipantFlowEvent]:
    """Convert a KIS aggregate investor snapshot into one event per cohort."""
    instrument = InstrumentId(
        venue=Venue.KIS,
        asset_class=AssetClass.FUTURE,
        raw_symbol=f"{market_code}:{market_subcode}",
        std_symbol=f"{market_code}:{market_subcode}",
        security_kind="market_aggregate",
        currency="KRW",
        exchange="KRX_DERIVATIVES",
    )
    minute = wallclock_ts_ns // 60_000_000_000
    events: list[ParticipantFlowEvent] = []
    for row_index, row in enumerate(rows):
        for prefix, label in _KIS_PARTICIPANT_GROUPS:
            relevant_keys = (
                f"{prefix}_shnu_vol", f"{prefix}_seln_vol", f"{prefix}_ntby_qty",
                f"{prefix}_shnu_tr_pbmn", f"{prefix}_seln_tr_pbmn", f"{prefix}_ntby_tr_pbmn",
            )
            # Do not invent a participant cohort when KIS has not supplied
            # that prefix in this response version.
            if not any(key in row for key in relevant_keys):
                continue
            buy_volume = _flow_decimal(row, f"{prefix}_shnu_vol")
            sell_volume = _flow_decimal(row, f"{prefix}_seln_vol")
            buy_value = _flow_decimal(row, f"{prefix}_shnu_tr_pbmn")
            sell_value = _flow_decimal(row, f"{prefix}_seln_tr_pbmn")
            net_volume = _flow_decimal(row, f"{prefix}_ntby_qty") if row.get(f"{prefix}_ntby_qty") not in (None, "") else buy_volume - sell_volume
            net_value = _flow_decimal(row, f"{prefix}_ntby_tr_pbmn") if row.get(f"{prefix}_ntby_tr_pbmn") not in (None, "") else buy_value - sell_value
            events.append(
                ParticipantFlowEvent(
                    instrument=instrument,
                    venue=Venue.KIS,
                    provider="kis",
                    source_ts_ns=None,
                    source_timestamp_kind="provider_snapshot_undated",
                    wallclock_ts_ns=wallclock_ts_ns,
                    receive_ts_ns=wallclock_ts_ns,
                    collector_run_id=collector_run_id,
                    native_event_id=f"{market_code}:{market_subcode}:{prefix}:{minute}:{row_index}",
                    collection_start_ts_ns=collection_start_ts_ns,
                    sequence=f"{minute}:{row_index}:{prefix}",
                    session="kr_derivatives",
                    market_code=market_code,
                    market_subcode=market_subcode,
                    participant=prefix,
                    participant_label=label,
                    buy_volume=buy_volume,
                    sell_volume=sell_volume,
                    net_volume=net_volume,
                    buy_value=buy_value,
                    sell_value=sell_value,
                    net_value=net_value,
                )
            )
    return events


KIS_REALTIME_COLUMNS: dict[str, list[str]] = {
    "H0BJASP0": [
        "stnd_iscd",
        "stck_cntg_hour",
        "askp_ert1",
        "bidp_ert1",
        "askp1",
        "bidp1",
        "askp_rsqn1",
        "bidp_rsqn1",
        "askp_ert2",
        "bidp_ert2",
        "askp2",
        "bidp2",
        "askp_rsqn2",
        "bidp_rsqn2",
        "askp_ert3",
        "bidp_ert3",
        "askp3",
        "bidp3",
        "askp_rsqn3",
        "bidp_rsqn3",
        "askp_ert4",
        "bidp_ert4",
        "askp4",
        "bidp4",
        "askp_rsqn4",
        "bidp_rsqn4",
        "askp_ert5",
        "bidp_ert5",
        "askp5",
        "bidp5",
        "askp_rsqn5",
        "bidp_rsqn5",
        "total_askp_rsqn",
        "total_bidp_rsqn",
    ],
    "H0BJCNT0": [
        "stnd_iscd",
        "bond_isnm",
        "stck_cntg_hour",
        "prdy_vrss_sign",
        "prdy_vrss",
        "prdy_ctrt",
        "stck_prpr",
        "cntg_vol",
        "stck_oprc",
        "stck_hgpr",
        "stck_lwpr",
        "stck_prdy_clpr",
        "bond_cntg_ert",
        "oprc_ert",
        "hgpr_ert",
        "lwpr_ert",
        "acml_vol",
        "prdy_vol",
        "cntg_type_cls_code",
    ],
    "H0BICNT0": [
        "nmix_id",
        "stnd_date1",
        "trnm_hour",
        "totl_ernn_nmix_oprc",
        "totl_ernn_nmix_hgpr",
        "totl_ernn_nmix_lwpr",
        "totl_ernn_nmix",
        "prdy_totl_ernn_nmix",
        "totl_ernn_nmix_prdy_vrss",
        "totl_ernn_nmix_prdy_vrss_sign",
        "totl_ernn_nmix_prdy_ctrt",
        "clen_prc_nmix",
        "mrkt_prc_nmix",
        "bond_call_rnvs_nmix",
        "bond_zero_rnvs_nmix",
        "bond_futs_thpr",
        "bond_avrg_drtn_val",
        "bond_avrg_cnvx_val",
        "bond_avrg_ytm_val",
        "bond_avrg_frdl_ytm_val",
    ],
    "HDFFF010": [
        "series_cd",
        "recv_date",
        "recv_time",
        "prev_price",
        "bid_qntt_1",
        "bid_num_1",
        "bid_price_1",
        "ask_qntt_1",
        "ask_num_1",
        "ask_price_1",
        "bid_qntt_2",
        "bid_num_2",
        "bid_price_2",
        "ask_qntt_2",
        "ask_num_2",
        "ask_price_2",
        "bid_qntt_3",
        "bid_num_3",
        "bid_price_3",
        "ask_qntt_3",
        "ask_num_3",
        "ask_price_3",
        "bid_qntt_4",
        "bid_num_4",
        "bid_price_4",
        "ask_qntt_4",
        "ask_num_4",
        "ask_price_4",
        "bid_qntt_5",
        "bid_num_5",
        "bid_price_5",
        "ask_qntt_5",
        "ask_num_5",
        "ask_price_5",
        "sttl_price",
    ],
    "HDFFF020": [
        "series_cd",
        "bsns_date",
        "mrkt_open_date",
        "mrkt_open_time",
        "mrkt_close_date",
        "mrkt_close_time",
        "prev_price",
        "recv_date",
        "recv_time",
        "active_flag",
        "last_price",
        "last_qntt",
        "prev_diff_price",
        "prev_diff_rate",
        "open_price",
        "high_price",
        "low_price",
        "vol",
        "prev_sign",
        "quotsign",
        "recv_time2",
        "psttl_price",
        "psttl_sign",
        "psttl_diff_price",
        "psttl_diff_rate",
    ],
    "H0IFCNT0": [
        "futs_shrn_iscd",
        "bsop_hour",
        "futs_prdy_vrss",
        "prdy_vrss_sign",
        "futs_prdy_ctrt",
        "futs_prpr",
        "futs_oprc",
        "futs_hgpr",
        "futs_lwpr",
        "last_cnqn",
        "acml_vol",
        "acml_tr_pbmn",
        "hts_thpr",
        "mrkt_basis",
        "dprt",
        "nmsc_fctn_stpl_prc",
        "fmsc_fctn_stpl_prc",
        "spead_prc",
        "hts_otst_stpl_qty",
        "otst_stpl_qty_icdc",
        "oprc_hour",
        "oprc_vrss_prpr_sign",
        "oprc_vrss_nmix_prpr",
        "hgpr_hour",
        "hgpr_vrss_prpr_sign",
        "hgpr_vrss_nmix_prpr",
        "lwpr_hour",
        "lwpr_vrss_prpr_sign",
        "lwpr_vrss_nmix_prpr",
        "shnu_rate",
        "cttr",
        "esdg",
        "otst_stpl_rgbf_qty_icdc",
        "thpr_basis",
        "futs_askp1",
        "futs_bidp1",
        "askp_rsqn1",
        "bidp_rsqn1",
        "seln_cntg_csnu",
        "shnu_cntg_csnu",
        "ntby_cntg_csnu",
        "seln_cntg_smtn",
        "shnu_cntg_smtn",
        "total_askp_rsqn",
        "total_bidp_rsqn",
        "prdy_vol_vrss_acml_vol_rate",
        "dscs_bltr_acml_qty",
        "dynm_mxpr",
        "dynm_llam",
        "dynm_prc_limt_yn",
    ],
    "H0IFASP0": [
        "futs_shrn_iscd",
        "bsop_hour",
        "futs_askp1",
        "futs_askp2",
        "futs_askp3",
        "futs_askp4",
        "futs_askp5",
        "futs_bidp1",
        "futs_bidp2",
        "futs_bidp3",
        "futs_bidp4",
        "futs_bidp5",
        "askp_csnu1",
        "askp_csnu2",
        "askp_csnu3",
        "askp_csnu4",
        "askp_csnu5",
        "bidp_csnu1",
        "bidp_csnu2",
        "bidp_csnu3",
        "bidp_csnu4",
        "bidp_csnu5",
        "askp_rsqn1",
        "askp_rsqn2",
        "askp_rsqn3",
        "askp_rsqn4",
        "askp_rsqn5",
        "bidp_rsqn1",
        "bidp_rsqn2",
        "bidp_rsqn3",
        "bidp_rsqn4",
        "bidp_rsqn5",
        "total_askp_csnu",
        "total_bidp_csnu",
        "total_askp_rsqn",
        "total_bidp_rsqn",
        "total_askp_rsqn_icdc",
        "total_bidp_rsqn_icdc",
    ],
    "H0IOCNT0": [
        "optn_shrn_iscd",
        "bsop_hour",
        "optn_prpr",
        "prdy_vrss_sign",
        "optn_prdy_vrss",
        "prdy_ctrt",
        "optn_oprc",
        "optn_hgpr",
        "optn_lwpr",
        "last_cnqn",
        "acml_vol",
        "acml_tr_pbmn",
        "hts_thpr",
        "hts_otst_stpl_qty",
        "otst_stpl_qty_icdc",
        "oprc_hour",
        "oprc_vrss_prpr_sign",
        "oprc_vrss_nmix_prpr",
        "hgpr_hour",
        "hgpr_vrss_prpr_sign",
        "hgpr_vrss_nmix_prpr",
        "lwpr_hour",
        "lwpr_vrss_prpr_sign",
        "lwpr_vrss_nmix_prpr",
        "shnu_rate",
        "prmm_val",
        "invl_val",
        "tmvl_val",
        "delta",
        "gama",
        "vega",
        "theta",
        "rho",
        "hts_ints_vltl",
        "esdg",
        "otst_stpl_rgbf_qty_icdc",
        "thpr_basis",
        "unas_hist_vltl",
        "cttr",
        "dprt",
        "mrkt_basis",
        "optn_askp1",
        "optn_bidp1",
        "askp_rsqn1",
        "bidp_rsqn1",
        "seln_cntg_csnu",
        "shnu_cntg_csnu",
        "ntby_cntg_csnu",
        "seln_cntg_smtn",
        "shnu_cntg_smtn",
        "total_askp_rsqn",
        "total_bidp_rsqn",
        "prdy_vol_vrss_acml_vol_rate",
        "avrg_vltl",
        "dscs_lrqn_vol",
        "dynm_mxpr",
        "dynm_llam",
        "dynm_prc_limt_yn",
    ],
    "H0IOASP0": [
        "optn_shrn_iscd",
        "bsop_hour",
        "optn_askp1",
        "optn_askp2",
        "optn_askp3",
        "optn_askp4",
        "optn_askp5",
        "optn_bidp1",
        "optn_bidp2",
        "optn_bidp3",
        "optn_bidp4",
        "optn_bidp5",
        "askp_csnu1",
        "askp_csnu2",
        "askp_csnu3",
        "askp_csnu4",
        "askp_csnu5",
        "bidp_csnu1",
        "bidp_csnu2",
        "bidp_csnu3",
        "bidp_csnu4",
        "bidp_csnu5",
        "askp_rsqn1",
        "askp_rsqn2",
        "askp_rsqn3",
        "askp_rsqn4",
        "askp_rsqn5",
        "bidp_rsqn1",
        "bidp_rsqn2",
        "bidp_rsqn3",
        "bidp_rsqn4",
        "bidp_rsqn5",
        "total_askp_csnu",
        "total_bidp_csnu",
        "total_askp_rsqn",
        "total_bidp_rsqn",
        "total_askp_rsqn_icdc",
        "total_bidp_rsqn_icdc",
    ],
    "H0MFASP0": [
        "futs_shrn_iscd",
        "bsop_hour",
        "futs_askp1",
        "futs_askp2",
        "futs_askp3",
        "futs_askp4",
        "futs_askp5",
        "futs_bidp1",
        "futs_bidp2",
        "futs_bidp3",
        "futs_bidp4",
        "futs_bidp5",
        "askp_csnu1",
        "askp_csnu2",
        "askp_csnu3",
        "askp_csnu4",
        "askp_csnu5",
        "bidp_csnu1",
        "bidp_csnu2",
        "bidp_csnu3",
        "bidp_csnu4",
        "bidp_csnu5",
        "askp_rsqn1",
        "askp_rsqn2",
        "askp_rsqn3",
        "askp_rsqn4",
        "askp_rsqn5",
        "bidp_rsqn1",
        "bidp_rsqn2",
        "bidp_rsqn3",
        "bidp_rsqn4",
        "bidp_rsqn5",
        "total_askp_csnu",
        "total_bidp_csnu",
        "total_askp_rsqn",
        "total_bidp_rsqn",
        "total_askp_rsqn_icdc",
        "total_bidp_rsqn_icdc",
    ],
}


STOCK_FUTURE_TRADE_COLUMNS = [
    "futs_shrn_iscd",
    "bsop_hour",
    "stck_prpr",
    "prdy_vrss_sign",
    "prdy_vrss",
    "futs_prdy_ctrt",
    "stck_oprc",
    "stck_hgpr",
    "stck_lwpr",
    "last_cnqn",
    "acml_vol",
    "acml_tr_pbmn",
    "hts_thpr",
    "mrkt_basis",
    "dprt",
    "nmsc_fctn_stpl_prc",
    "fmsc_fctn_stpl_prc",
    "spead_prc",
    "hts_otst_stpl_qty",
    "otst_stpl_qty_icdc",
    "oprc_hour",
    "oprc_vrss_prpr_sign",
    "oprc_vrss_prpr",
    "hgpr_hour",
    "hgpr_vrss_prpr_sign",
    "hgpr_vrss_prpr",
    "lwpr_hour",
    "lwpr_vrss_prpr_sign",
    "lwpr_vrss_prpr",
    "shnu_rate",
    "cttr",
    "esdg",
    "otst_stpl_rgbf_qty_icdc",
    "thpr_basis",
    "askp1",
    "bidp1",
    "askp_rsqn1",
    "bidp_rsqn1",
    "seln_cntg_csnu",
    "shnu_cntg_csnu",
    "ntby_cntg_csnu",
    "seln_cntg_smtn",
    "shnu_cntg_smtn",
    "total_askp_rsqn",
    "total_bidp_rsqn",
    "prdy_vol_vrss_acml_vol_rate",
    "dynm_mxpr",
    "dynm_llam",
    "dynm_prc_limt_yn",
]

STOCK_FUTURE_BOOK_COLUMNS = [
    "futs_shrn_iscd",
    "bsop_hour",
    *[f"askp{i}" for i in range(1, 11)],
    *[f"bidp{i}" for i in range(1, 11)],
    *[f"askp_csnu{i}" for i in range(1, 11)],
    *[f"bidp_csnu{i}" for i in range(1, 11)],
    *[f"askp_rsqn{i}" for i in range(1, 11)],
    *[f"bidp_rsqn{i}" for i in range(1, 11)],
    "total_askp_csnu",
    "total_bidp_csnu",
    "total_askp_rsqn",
    "total_bidp_rsqn",
    "total_askp_rsqn_icdc",
    "total_bidp_rsqn_icdc",
]

STOCK_OPTION_BOOK_COLUMNS = [
    "optn_shrn_iscd",
    "bsop_hour",
    *[f"optn_askp{i}" for i in range(1, 6)],
    *[f"optn_bidp{i}" for i in range(1, 6)],
    *[f"askp_csnu{i}" for i in range(1, 6)],
    *[f"bidp_csnu{i}" for i in range(1, 6)],
    *[f"askp_rsqn{i}" for i in range(1, 6)],
    *[f"bidp_rsqn{i}" for i in range(1, 6)],
    "total_askp_csnu",
    "total_bidp_csnu",
    "total_askp_rsqn",
    "total_bidp_rsqn",
    "total_askp_rsqn_icdc",
    "total_bidp_rsqn_icdc",
    *[f"optn_askp{i}" for i in range(6, 11)],
    *[f"optn_bidp{i}" for i in range(6, 11)],
    *[f"askp_csnu{i}" for i in range(6, 11)],
    *[f"bidp_csnu{i}" for i in range(6, 11)],
    *[f"askp_rsqn{i}" for i in range(6, 11)],
    *[f"bidp_rsqn{i}" for i in range(6, 11)],
]


for tr_id in ("H0CFCNT0", "H0MFCNT0"):
    KIS_REALTIME_COLUMNS.setdefault(tr_id, KIS_REALTIME_COLUMNS["H0IFCNT0"])
for tr_id in ("H0CFASP0", "H0EUASP0"):
    KIS_REALTIME_COLUMNS.setdefault(tr_id, KIS_REALTIME_COLUMNS["H0IFASP0" if tr_id == "H0CFASP0" else "H0IOASP0"])
KIS_REALTIME_COLUMNS.setdefault("H0EUCNT0", KIS_REALTIME_COLUMNS["H0IOCNT0"])
KIS_REALTIME_COLUMNS.setdefault("H0ZFCNT0", STOCK_FUTURE_TRADE_COLUMNS)
KIS_REALTIME_COLUMNS.setdefault("H0ZFASP0", STOCK_FUTURE_BOOK_COLUMNS)
KIS_REALTIME_COLUMNS.setdefault("H0ZOCNT0", KIS_REALTIME_COLUMNS["H0IOCNT0"])
KIS_REALTIME_COLUMNS.setdefault("H0ZOASP0", STOCK_OPTION_BOOK_COLUMNS)


KIS_REALTIME_COLUMNS['H0UPCNT0'] = [
    'bstp_cls_code','bsop_hour','prpr_nmix','prdy_vrss_sign','bstp_nmix_prdy_vrss',
    'acml_vol','acml_tr_pbmn','pcas_vol','pcas_tr_pbmn','prdy_ctrt','oprc_nmix',
    'nmix_hgpr','nmix_lwpr','oprc_vrss_nmix_prpr','oprc_vrss_nmix_sign',
    'hgpr_vrss_nmix_prpr','hgpr_vrss_nmix_sign','lwpr_vrss_nmix_prpr',
    'lwpr_vrss_nmix_sign','prdy_clpr_vrss_oprc_rate','prdy_clpr_vrss_hgpr_rate',
    'prdy_clpr_vrss_lwpr_rate','uplm_issu_cnt','ascn_issu_cnt','stnr_issu_cnt',
    'down_issu_cnt','lslm_issu_cnt','qtqt_ascn_issu_cnt','qtqt_down_issu_cnt','tick_vrss']
TRADE_TR_IDS = {"H0BJCNT0", "H0BICNT0", "HDFFF020", "H0IFCNT0", "H0IOCNT0", "H0CFCNT0", "H0MFCNT0", "H0EUCNT0", "H0ZFCNT0", "H0ZOCNT0"}
BOOK_TR_IDS = {"H0BJASP0", "HDFFF010", "H0IFASP0", "H0IOASP0", "H0MFASP0", "H0CFASP0", "H0EUASP0", "H0ZFASP0", "H0ZOASP0"}
OPTION_TR_IDS = {"H0IOCNT0", "H0IOASP0", "H0EUCNT0", "H0EUASP0", "H0ZOCNT0", "H0ZOASP0"}
BOND_TR_IDS = {"H0BJASP0", "H0BJCNT0", "H0BICNT0"}
OVERSEAS_FUTURE_TR_IDS = {"HDFFF010", "HDFFF020"}


@dataclass(frozen=True)
class KisSubscription:
    tr_id: str
    tr_key: str
    tr_type: str = "1"


def parse_kis_subscriptions(value: str | list[str] | None) -> list[KisSubscription]:
    if value is None:
        return []
    tokens = value.split(",") if isinstance(value, str) else value
    subscriptions: list[KisSubscription] = []
    for raw_token in tokens:
        token = str(raw_token or "").strip()
        if not token:
            continue
        tr_type = "1"
        if token.count(":") >= 2:
            tr_type, tr_id, tr_key = [part.strip() for part in token.split(":", 2)]
        elif ":" in token:
            tr_id, tr_key = [part.strip() for part in token.split(":", 1)]
        elif "=" in token:
            tr_id, tr_key = [part.strip() for part in token.split("=", 1)]
        else:
            subscriptions.append(KisSubscription("H0IFASP0", token))
            subscriptions.append(KisSubscription("H0IFCNT0", token))
            continue
        if tr_id not in KIS_REALTIME_COLUMNS:
            raise ValueError(f"Unsupported KIS realtime tr_id: {tr_id}")
        if not tr_key:
            raise ValueError("KIS tr_key is empty")
        subscriptions.append(KisSubscription(tr_id=tr_id, tr_key=tr_key, tr_type=tr_type or "1"))
    return subscriptions


def kis_subscription_payload(approval_key: str, subscription: KisSubscription) -> dict[str, Any]:
    return {
        "header": {
            "approval_key": approval_key,
            "custtype": "P",
            "tr_type": subscription.tr_type,
            "content-type": "utf-8",
        },
        "body": {
            "input": {
                "tr_id": subscription.tr_id,
                "tr_key": subscription.tr_key,
            }
        },
    }


def kis_unsubscription_payload(approval_key: str, subscription: KisSubscription) -> dict[str, Any]:
    """Build the KIS WebSocket unsubscribe frame (tr_type=2)."""
    return kis_subscription_payload(
        approval_key,
        KisSubscription(tr_id=subscription.tr_id, tr_key=subscription.tr_key, tr_type="2"),
    )


def parse_kis_realtime_frames(raw: str) -> list[tuple[str, dict[str, str]]]:
    if not raw or raw.startswith("{"):
        return []
    parts = raw.split("|", 3)
    if len(parts) < 4:
        return []
    tr_id = parts[1]
    columns = KIS_REALTIME_COLUMNS.get(tr_id)
    if not columns:
        return []
    values = parts[3].split("^")
    if not values:
        return []
    data_count = int(parts[2]) if parts[2].isdigit() else 1
    width = len(columns)
    if data_count <= 1 or len(values) <= width:
        return [(tr_id, dict(zip(columns, values)))]
    rows: list[tuple[str, dict[str, str]]] = []
    for index in range(data_count):
        start = index * width
        chunk = values[start : start + width]
        if not chunk:
            continue
        rows.append((tr_id, dict(zip(columns, chunk))))
    return rows


def parse_kis_realtime_frame(raw: str) -> tuple[str, dict[str, str]] | None:
    rows = parse_kis_realtime_frames(raw)
    if not rows:
        return None
    return rows[0]


def kis_row_to_event(tr_id: str, row: dict[str, str], *, receive_ts_ns: int | None = None, receive_monotonic_ns: int | None = None) -> TradeEvent | OrderBookEvent:
    if tr_id == 'H0UPCNT0':
        return IndexQuoteEvent(
            instrument=InstrumentId(venue=Venue.KIS,asset_class=AssetClass.INDEX,
                raw_symbol=row['bstp_cls_code'],std_symbol='KOSPI200',exchange='KRX',currency='KRW'),
            venue=Venue.KIS,provider='kis',source_ts_ns=_source_ts_ns(row),
            receive_ts_ns=receive_ts_ns or utc_now_ns(),wallclock_ts_ns=receive_ts_ns,
            receive_monotonic_ns=receive_monotonic_ns,session='kr_cash_index',
            sequence=row['bsop_hour'],value=Decimal(row['prpr_nmix']),
            open=Decimal(row['oprc_nmix']),high=Decimal(row['nmix_hgpr']),
            low=Decimal(row['nmix_lwpr']),metadata={'tr_id':tr_id,'native':row})
    if tr_id in TRADE_TR_IDS:
        return _trade_event(tr_id, row, receive_ts_ns=receive_ts_ns, receive_monotonic_ns=receive_monotonic_ns)
    if tr_id in BOOK_TR_IDS:
        return _book_event(tr_id, row, receive_ts_ns=receive_ts_ns, receive_monotonic_ns=receive_monotonic_ns)
    raise ValueError(f"Unsupported KIS realtime tr_id: {tr_id}")


def _instrument(tr_id: str, row: dict[str, str]) -> InstrumentId:
    raw_symbol = row.get("futs_shrn_iscd") or row.get("optn_shrn_iscd") or row.get("series_cd") or row.get("stnd_iscd") or row.get("nmix_id") or ""
    if tr_id in OPTION_TR_IDS:
        asset_class = AssetClass.OPTION
    elif tr_id in BOND_TR_IDS:
        asset_class = AssetClass.BOND_YIELD
    else:
        asset_class = AssetClass.FUTURE
    return InstrumentId(
        venue=Venue.KIS,
        asset_class=asset_class,
        raw_symbol=raw_symbol,
        std_symbol=f"KIS:{raw_symbol}",
        exchange="CME" if tr_id in OVERSEAS_FUTURE_TR_IDS else "KRX",
        currency="USD" if tr_id in OVERSEAS_FUTURE_TR_IDS else "KRW",
    )


def _source_ts_ns(row: dict[str, str]) -> int:
    recv_date = row.get("recv_date") or row.get("bsns_date") or ""
    recv_time = row.get("recv_time") or row.get("recv_time2") or ""
    if len(recv_date) == 8 and recv_date.isdigit() and len(recv_time) >= 6 and recv_time[:6].isdigit():
        dt = datetime.strptime(recv_date + recv_time[:6], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1_000_000_000)
    value = row.get("bsop_hour") or row.get("stck_cntg_hour") or row.get("trnm_hour") or ""
    if len(value) >= 6 and value[:6].isdigit():
        now = datetime.now(KST)
        event_dt = now.replace(
            hour=int(value[0:2]),
            minute=int(value[2:4]),
            second=int(value[4:6]),
            microsecond=0,
        )
        if event_dt - now > timedelta(hours=12):
            event_dt -= timedelta(days=1)
        elif now - event_dt > timedelta(hours=12):
            event_dt += timedelta(days=1)
        return int(event_dt.astimezone(timezone.utc).timestamp() * 1_000_000_000)
    return utc_now_ns()


def _decimal(row: dict[str, str], key: str) -> Decimal:
    value = (row.get(key) or "0").strip()
    if not value:
        value = "0"
    return Decimal(value)


def _first_decimal(row: dict[str, str], keys: tuple[str, ...]) -> Decimal:
    for key in keys:
        if row.get(key) not in (None, ""):
            return _decimal(row, key)
    return Decimal("0")


def _trade_event(tr_id: str, row: dict[str, str], *, receive_ts_ns: int | None = None, receive_monotonic_ns: int | None = None) -> TradeEvent:
    qty = _first_decimal(row, ("last_cnqn", "last_qntt", "cntg_vol", "vol", "antc_cnqn"))
    return TradeEvent(
        instrument=_instrument(tr_id, row),
        venue=Venue.KIS,
        provider="kis",
        source_ts_ns=_source_ts_ns(row),
        receive_ts_ns=receive_ts_ns or utc_now_ns(),
        wallclock_ts_ns=receive_ts_ns,
        receive_monotonic_ns=receive_monotonic_ns,
        session="overseas_futures" if tr_id in OVERSEAS_FUTURE_TR_IDS else "kr_bond" if tr_id in BOND_TR_IDS else "kr_derivatives",
        sequence=row.get("bsop_hour") or row.get("stck_cntg_hour") or row.get("trnm_hour") or row.get("recv_time"),
        price=_first_decimal(row, ("optn_prpr", "futs_prpr", "stck_prpr", "last_price", "totl_ernn_nmix", "bond_avrg_ytm_val", "antc_cnpr")),
        quantity=abs(qty),
        aggressor_side=Side.UNKNOWN,
        trade_id=row.get("bsop_hour") or row.get("stck_cntg_hour") or row.get("trnm_hour") or row.get("recv_time"),
    )


def _book_event(tr_id: str, row: dict[str, str], *, receive_ts_ns: int | None = None, receive_monotonic_ns: int | None = None) -> OrderBookEvent:
    asks: list[OrderBookLevel] = []
    bids: list[OrderBookLevel] = []
    for index in range(1, 11):
        ask_price = _price_level(row, "ask", index)
        bid_price = _price_level(row, "bid", index)
        if ask_price is not None:
            asks.append(OrderBookLevel(price=ask_price, quantity=_quantity_level(row, "ask", index)))
        if bid_price is not None:
            bids.append(OrderBookLevel(price=bid_price, quantity=_quantity_level(row, "bid", index)))
    return OrderBookEvent(
        instrument=_instrument(tr_id, row),
        venue=Venue.KIS,
        provider="kis",
        source_ts_ns=_source_ts_ns(row),
        receive_ts_ns=receive_ts_ns or utc_now_ns(),
        wallclock_ts_ns=receive_ts_ns,
        receive_monotonic_ns=receive_monotonic_ns,
        session="overseas_futures" if tr_id in OVERSEAS_FUTURE_TR_IDS else "kr_bond" if tr_id in BOND_TR_IDS else "kr_derivatives",
        sequence=row.get("bsop_hour") or row.get("stck_cntg_hour") or row.get("recv_time"),
        update_type=BookUpdateType.SNAPSHOT,
        depth=max(len(asks), len(bids)),
        asks=asks,
        bids=bids,
        is_full_depth=len(asks) >= 10 and len(bids) >= 10,
    )


def _price_level(row: dict[str, str], side: str, index: int) -> Decimal | None:
    candidates = (
        f"optn_{side}p{index}",
        f"futs_{side}p{index}",
        f"{side}p{index}",
        f"{side}_price_{index}",
    )
    for key in candidates:
        value = row.get(key)
        if value not in (None, ""):
            return _decimal(row, key)
    return None


def _quantity_level(row: dict[str, str], side: str, index: int) -> Decimal:
    candidates = (
        f"{side}p_rsqn{index}",
        f"{side}_qntt_{index}",
    )
    for key in candidates:
        if row.get(key) not in (None, ""):
            return _decimal(row, key)
    return Decimal("0")


class KisCollector:
    name = "kis"

    def __init__(
        self,
        subscriptions: list[KisSubscription] | None = None,
        *,
        auto_commodity: bool = False,
        commodity_products: tuple[str, ...] = ("B10", "USD"),
        auto_index_derivatives: bool = False,
        index_underlyings: tuple[str, ...] = ("KOSPI200", "KOSDAQ150"),
        auto_overseas_futures: bool = False,
        overseas_future_products: tuple[str, ...] = ("NQ",),
        participant_flow_enabled: bool = False,
    ) -> None:
        self.subscriptions = subscriptions or []
        self.auto_commodity = auto_commodity
        self.commodity_products = commodity_products
        self.auto_index_derivatives = auto_index_derivatives
        self.index_underlyings = index_underlyings
        self.auto_overseas_futures = auto_overseas_futures
        self.overseas_future_products = overseas_future_products
        self.participant_flow_enabled = participant_flow_enabled
        from feeds.universe_config import load_universe_config
        self.cash_index_enabled = bool(auto_index_derivatives and
            load_universe_config().get('dynamic',{}).get('kis_cash_index',{}).get('enabled',False))

    async def run(self, bus: EventBus, duration_sec: float | None = None) -> None:
        if self.auto_overseas_futures or any(item.tr_id in OVERSEAS_FUTURE_TR_IDS for item in self.subscriptions):
            raise RuntimeError("KIS CME real-time futures feed requires paid market-data entitlement and is disabled")
        if self.participant_flow_enabled:
            raise RuntimeError("KIS stock participant flow cannot be used as futures foreign flow")
        if not self.subscriptions and not self.auto_commodity and not self.auto_index_derivatives and not self.auto_overseas_futures:
            raise RuntimeError("KIS collector needs --symbols TRID:TR_KEY, auto:commodity, auto:index-derivatives, or auto:overseas-nq")
        client = KisAuthClient()
        approval_key = await client.issue_approval_key()
        deadline = asyncio.get_running_loop().time() + duration_sec if duration_sec else None
        collection_start_ts_ns = time.time_ns()
        participant_task: asyncio.Task[None] | None = None
        index_task = None
        if self.cash_index_enabled:
            from feeds.kis_index import KisIndexFeed
            index_task = asyncio.create_task(KisIndexFeed(client,client.settings).run(bus),name='kis-cash-index')
        if self.participant_flow_enabled and self._participant_flow_config() is not None:
            participant_task = asyncio.create_task(
                self._participant_flow_loop(bus, client, deadline, collection_start_ts_ns),
                name="kis-participant-flow",
            )
        try:
            if self.auto_commodity or self.auto_index_derivatives or self.auto_overseas_futures:
                desired = set()
                if self.auto_commodity:
                    desired |= await self._resolve_commodity_subscriptions()
                if self.auto_index_derivatives:
                    desired |= await self._resolve_index_derivative_subscriptions()
                if self.auto_overseas_futures:
                    desired |= await self._resolve_overseas_future_subscriptions()
            else:
                desired = set(self.subscriptions)
            if self.cash_index_enabled:
                desired.add(KisSubscription('H0UPCNT0','2001'))

            # KIS limits the number of realtime registrations per socket.  The
            # selected derivatives universe can exceed that limit, so use independent
            # sockets per shard while preserving the complete selected universe.
            if (self.auto_commodity or self.auto_index_derivatives or self.auto_overseas_futures) and len(desired) > 40:
                await self._run_sharded(bus, client, approval_key, desired, deadline)
                return

            async with websockets.connect(client.endpoints.ws_url, ping_interval=None, max_queue=512) as ws:
                active: set[KisSubscription] = set()
                await self._sync_subscriptions(ws, approval_key, active, desired, client.endpoints.use_mock)
                last_refresh = asyncio.get_running_loop().time()
                logger.info(
                    "KIS collector subscribed to %d streams mock=%s auto_commodity=%s auto_index_derivatives=%s auto_overseas_futures=%s",
                    len(active),
                    client.endpoints.use_mock,
                    self.auto_commodity,
                    self.auto_index_derivatives,
                    self.auto_overseas_futures,
                )
                while True:
                    if index_task is not None and index_task.done():
                        index_task.result()
                    if deadline and asyncio.get_running_loop().time() >= deadline:
                        break
                    if (self.auto_commodity or self.auto_index_derivatives or self.auto_overseas_futures) and asyncio.get_running_loop().time() - last_refresh >= self._refresh_interval_sec():
                        desired = set()
                        if self.auto_commodity:
                            desired |= await self._resolve_commodity_subscriptions()
                        if self.auto_index_derivatives:
                            desired |= await self._resolve_index_derivative_subscriptions()
                        if self.auto_overseas_futures:
                            desired |= await self._resolve_overseas_future_subscriptions()
                        if self.cash_index_enabled:
                            desired.add(KisSubscription('H0UPCNT0','2001'))
                        await self._sync_subscriptions(ws, approval_key, active, desired, client.endpoints.use_mock)
                        last_refresh = asyncio.get_running_loop().time()
                    timeout = max(0.1, deadline - asyncio.get_running_loop().time()) if deadline else None
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except asyncio.TimeoutError:
                        break
                    except websockets.exceptions.ConnectionClosed as exc:
                        raise RuntimeError(f"KIS websocket closed code={exc.code} reason={exc.reason or 'unspecified'}") from exc
                    received_ns=time.time_ns(); received_monotonic_ns=time.monotonic_ns()
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    if await self._handle_system_message(ws, raw):
                        continue
                    for tr_id, row in parse_kis_realtime_frames(raw):
                        event = kis_row_to_event(tr_id, row, receive_ts_ns=received_ns, receive_monotonic_ns=received_monotonic_ns)
                        await bus.publish_market_event(event)
        finally:
            if index_task is not None:
                index_task.cancel()
                await asyncio.gather(index_task,return_exceptions=True)
            if participant_task is not None:
                participant_task.cancel()
                await asyncio.gather(participant_task, return_exceptions=True)

    def _participant_flow_config(self) -> dict[str, Any] | None:
        from feeds.universe_config import load_universe_config

        config = load_universe_config().get("dynamic", {}).get("kis_participant_flow", {})
        if not isinstance(config, dict) or not bool(config.get("enabled", False)):
            return None
        subcodes = config.get("market_subcodes", [])
        if isinstance(subcodes, str):
            subcodes = [subcodes]
        selected = [str(value).strip() for value in subcodes if str(value).strip()]
        if not selected:
            logger.warning("KIS participant-flow configuration is enabled but has no market_subcodes")
            return None
        return {
            "market_code": str(config.get("market_code", "K2I")).strip().upper(),
            "market_subcodes": selected,
            "interval_seconds": max(60, int(config.get("interval_seconds", 60))),
        }

    async def _participant_flow_loop(
        self,
        bus: EventBus,
        client: KisAuthClient,
        deadline: float | None,
        collection_start_ts_ns: int,
    ) -> None:
        """Poll the KIS market snapshot once per configured interval.

        Polling begins at the next UTC minute boundary and uses a single
        sequential request per configured market.  A failed request never
        stops the WebSocket collector or manufactures a missing observation.
        """
        config = self._participant_flow_config()
        if config is None:
            return
        interval = int(config["interval_seconds"])
        loop = asyncio.get_running_loop()
        next_due_ns = ((time.time_ns() // (interval * 1_000_000_000)) + 1) * interval * 1_000_000_000
        while True:
            delay = max(0.0, (next_due_ns - time.time_ns()) / 1_000_000_000)
            if deadline is not None:
                delay = min(delay, max(0.0, deadline - loop.time()))
            if delay:
                await asyncio.sleep(delay)
            if deadline is not None and loop.time() >= deadline:
                return
            for subcode in config["market_subcodes"]:
                try:
                    rows = await client.inquire_investor_time_by_market(
                        market_code=config["market_code"], market_subcode=subcode
                    )
                    wallclock_ts_ns = time.time_ns()
                    events = kis_participant_flow_events(
                        rows,
                        market_code=config["market_code"],
                        market_subcode=subcode,
                        wallclock_ts_ns=wallclock_ts_ns,
                        collector_run_id=f"kis-flow-{collection_start_ts_ns}",
                        collection_start_ts_ns=collection_start_ts_ns,
                    )
                    if not events:
                        logger.warning("KIS participant-flow returned no rows market=%s subcode=%s", config["market_code"], subcode)
                    for event in events:
                        await bus.publish_market_event(event)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("KIS participant-flow poll failed market=%s subcode=%s", config["market_code"], subcode)
            next_due_ns += interval * 1_000_000_000
            while next_due_ns <= time.time_ns():
                next_due_ns += interval * 1_000_000_000


    async def _run_sharded(
        self,
        bus: EventBus,
        client: KisAuthClient,
        approval_key: str,
        desired: set[KisSubscription],
        deadline: float | None,
    ) -> None:
        shard_size = 40
        shards = [list(items) for items in (sorted(desired, key=lambda item: (item.tr_id, item.tr_key))[i : i + shard_size] for i in range(0, len(desired), shard_size))]
        # Approval keys are session credentials. Reusing one key across
        # simultaneous sockets can make KIS close an existing connection when
        # the next shard logs in. Issue one key per shard, sequentially to
        # avoid an authentication burst.
        approval_keys = [approval_key]
        for _ in range(1, len(shards)):
            await asyncio.sleep(0.25)
            approval_keys.append(await client.issue_approval_key())

        async def consume(items: list[KisSubscription], index: int) -> None:
            await asyncio.sleep(index * 0.5)
            async with websockets.connect(client.endpoints.ws_url, ping_interval=None, max_queue=512) as ws:
                active: set[KisSubscription] = set()
                await self._sync_subscriptions(ws, approval_keys[index], active, set(items), client.endpoints.use_mock)
                logger.info("KIS collector shard=%d/%d subscribed to %d streams", index + 1, len(shards), len(active))
                while True:
                    if deadline and asyncio.get_running_loop().time() >= deadline:
                        return
                    timeout = max(0.1, deadline - asyncio.get_running_loop().time()) if deadline else None
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except asyncio.TimeoutError:
                        return
                    except websockets.exceptions.ConnectionClosed as exc:
                        raise RuntimeError(f"KIS websocket shard={index + 1} closed code={exc.code} reason={exc.reason or 'unspecified'}") from exc
                    received_ns=time.time_ns(); received_monotonic_ns=time.monotonic_ns()
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    if await self._handle_system_message(ws, raw):
                        continue
                    for tr_id, row in parse_kis_realtime_frames(raw):
                        event = kis_row_to_event(tr_id, row, receive_ts_ns=received_ns, receive_monotonic_ns=received_monotonic_ns)
                        await bus.publish_market_event(event)

        await asyncio.gather(*(consume(items, index) for index, items in enumerate(shards)))

    async def _resolve_commodity_subscriptions(self) -> set[KisSubscription]:
        from feeds.providers.universe import InstrumentUniverseResolver

        resolved = await InstrumentUniverseResolver().resolve_kis_commodity_futures(products=self.commodity_products)
        subscriptions: set[KisSubscription] = set()
        for instruments in resolved.values():
            for instrument in instruments:
                subscriptions.add(KisSubscription("H0CFCNT0", instrument.raw_symbol))
                subscriptions.add(KisSubscription("H0CFASP0", instrument.raw_symbol))
        if not subscriptions:
            raise RuntimeError("KIS commodity master returned no current B10/USD contracts")
        return subscriptions

    async def _resolve_overseas_future_subscriptions(self) -> set[KisSubscription]:
        from feeds.providers.universe import InstrumentUniverseResolver

        resolved = await InstrumentUniverseResolver().resolve_kis_overseas_futures(products=self.overseas_future_products)
        subscriptions: set[KisSubscription] = set()
        for instruments in resolved.values():
            for instrument in instruments:
                subscriptions.add(KisSubscription("HDFFF020", instrument.raw_symbol))
                subscriptions.add(KisSubscription("HDFFF010", instrument.raw_symbol))
        if not subscriptions:
            raise RuntimeError("KIS overseas futures master returned no current contracts")
        return subscriptions

    async def _resolve_index_derivative_subscriptions(self) -> set[KisSubscription]:
        from feeds.providers.universe import InstrumentUniverseResolver
        from feeds.universe_config import load_universe_config

        resolver = InstrumentUniverseResolver()
        policy = load_universe_config().get("dynamic", {}).get("kis_index_derivatives", {})
        strikes_each_side = max(0, int(policy.get("strikes_each_side", 1))) if isinstance(policy, dict) else 1
        subscriptions: set[KisSubscription] = set()
        for underlying in self.index_underlyings:
            resolved = await resolver.resolve_kis_index_derivatives(underlying=underlying, strikes_each_side=strikes_each_side)
            for instrument in resolved.get("futures", []):
                # Both domestic index futures use H0IF. H0EU is night options.
                tr_prefix = "H0IF"
                subscriptions.add(KisSubscription(tr_prefix + "CNT0", instrument.raw_symbol))
                subscriptions.add(KisSubscription(tr_prefix + "ASP0", instrument.raw_symbol))
            # KOSPI200 options are the supported option universe. The master
            # already selected the two expiries and ATM +/- 5 strikes.
            if underlying.upper() == "KOSPI200" and policy.get("collect_options", False):
                for instrument in resolved.get("options", []):
                    subscriptions.add(KisSubscription("H0IOCNT0", instrument.raw_symbol))
                    subscriptions.add(KisSubscription("H0IOASP0", instrument.raw_symbol))
        if not subscriptions:
            raise RuntimeError("KIS index derivative master returned no current contracts")
        return subscriptions

    def _refresh_interval_sec(self) -> float:
        if self.auto_index_derivatives:
            # ATM is re-evaluated every minute; _sync_subscriptions only sends
            # frames when the selected contract set actually changes.
            return 60.0
        # Avoid downloading the master on every tick.  During ordinary days
        # the cached selection is sufficient; refresh frequently only in the
        # contract-roll window (late month and the first few session days).
        day = datetime.now(KST).day
        return 900.0 if day >= 20 or day <= 5 else 7 * 86400.0

    async def _sync_subscriptions(
        self,
        ws: Any,
        approval_key: str,
        active: set[KisSubscription],
        desired: set[KisSubscription],
        use_mock: bool,
    ) -> None:
        # Subscribe new contracts first so rollover has no data gap.
        for subscription in sorted(desired - active, key=lambda item: (item.tr_id, item.tr_key)):
            await _send_kis_subscription_frame(
                ws,
                kis_subscription_payload(approval_key, subscription),
                use_mock=use_mock,
            )
        for subscription in sorted(active - desired, key=lambda item: (item.tr_id, item.tr_key)):
            await _send_kis_subscription_frame(
                ws,
                kis_unsubscription_payload(approval_key, subscription),
                use_mock=use_mock,
            )
        active.clear()
        active.update(desired)

    async def _handle_system_message(self, ws: Any, raw: str) -> bool:
        if not raw or raw[0] in {"0", "1"}:
            return False
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("KIS unknown non-market frame: %s", raw[:200])
            return True
        header = payload.get("header") or {}
        tr_id = header.get("tr_id")
        if tr_id == "PINGPONG":
            await ws.pong(raw)
            return True
        body = payload.get("body") or {}
        rt_cd = str(body.get("rt_cd", ""))
        msg = str(body.get("msg1", ""))
        if rt_cd == "0" or msg == "ALREADY IN SUBSCRIBE":
            logger.debug("KIS subscription/system ok tr_id=%s tr_key=%s msg=%s", tr_id, header.get("tr_key"), msg)
            return True
        if rt_cd:
            raise RuntimeError(f"KIS websocket subscription failed tr_id={tr_id} tr_key={header.get('tr_key')} code={rt_cd} msg={msg}")
        return True


class KisParticipantFlowCollector:
    """Dedicated, rate-limited KIS REST collector for investor flow.

    It intentionally runs separately from KIS WebSocket collection: a short
    WebSocket reconnect must not cancel a one-minute REST sampling schedule.
    """

    name = "kis-flow"

    async def run(self, bus: EventBus, duration_sec: float | None = None) -> None:
        helper = KisCollector()
        if helper._participant_flow_config() is None:
            raise RuntimeError("KIS participant-flow collector is disabled or missing market_subcodes")
        deadline = asyncio.get_running_loop().time() + duration_sec if duration_sec else None
        collection_start_ts_ns = time.time_ns()
        await helper._participant_flow_loop(bus, KisAuthClient(), deadline, collection_start_ts_ns)
