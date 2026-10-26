from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pyarrow.parquet as pq

from market.market_policy import MarketDataPolicyBus
from core.models import EventType, Venue
from storage.parquet_store import ParquetBatchWriter
from feeds.providers.ls_market import LSReadOnlyClient, krx_futures_session, parse_dbt_foreign, parse_delayed_future_trade
from runtime.settings import RuntimeSettings
from storage.sqlite_store import RuntimeStore


KST = timezone(timedelta(hours=9))


def test_krx_day_and_night_subscriptions_keep_separate_trading_dates():
    assert krx_futures_session(datetime(2026, 9, 28, 10, tzinfo=KST)) == ("DFK2I", "2026-09-28")
    assert krx_futures_session(datetime(2026, 9, 28, 20, tzinfo=KST)) == ("NFK2I", "2026-09-29")
    assert krx_futures_session(datetime(2026, 9, 29, 5, tzinfo=KST)) == ("NFK2I", "2026-09-29")
    assert krx_futures_session(datetime(2026, 9, 27, 20, tzinfo=KST)) is None


def test_dbt_uses_foreign_group_two_and_preserves_native_units():
    frame = {"header": {"tr_cd": "DBT", "tr_key": "DFK2I"}, "body": {
        "tjjtime": "10:00:00", "tjjcode2": "0017",
        "msvolume2": "120", "mdvolume2": "90", "msvol2": "30",
        "msvalue2": "55", "mdvalue2": "40", "msval2": "15",
    }}
    event = parse_dbt_foreign(frame, "DFK2I", "2026-09-28", 123456789)
    assert event is not None
    assert event.event_type == EventType.PARTICIPANT_FLOW
    assert event.venue == Venue.KRX and event.instrument.std_symbol == "KOSPI200_FUTURES"
    assert (event.buy_volume, event.sell_volume, event.net_volume) == (Decimal(120), Decimal(90), Decimal(30))
    assert event.value_unit == "LS_native_unverified" and event.source_ts_ns is None
    assert MarketDataPolicyBus(object())._allowed(event)
    assert parse_dbt_foreign(frame, "NFK2I", "2026-09-29") is None
    bad = {**frame, "body": {**frame["body"], "msvol2": "31"}}
    with pytest.raises(ValueError, match="disagree"):
        parse_dbt_foreign(bad, "DFK2I", "2026-09-28")


def test_overseas_quote_requires_measured_delay_and_exact_contract():
    master = {"Symbol": "ESZ26", "BscGdsCd": "ES", "LstngYr": "2026", "LstngM": "12"}
    row = {"Symbol": "ESZ26", "KorDate": "20260928", "RcvTm": "100000", "TrdP": "6123.25", "TrdQ": "4"}
    received = int(datetime(2026, 9, 28, 10, 15, tzinfo=KST).timestamp() * 1e9)
    event = parse_delayed_future_trade(row, "ES", master, received)
    assert event is not None
    assert event.observed_delay_ms == 900_000
    assert event.source_timestamp_kind == "ls_korean_trade_time"
    assert event.instrument.contract_month == "202612" and event.price == Decimal("6123.25")
    assert MarketDataPolicyBus(object())._allowed(event)
    assert parse_delayed_future_trade(row, "ES", master, received - 12 * 60 * 1_000_000_000) is None
    with pytest.raises(ValueError, match="symbol differs"):
        parse_delayed_future_trade({**row, "Symbol": "NQZ26"}, "ES", master, received)


def test_credentials_alone_cannot_enable_ls_data(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\n"
        "LS_APP_KEY=example\nLS_APP_SECRET=example\n",
        encoding="utf-8",
    )
    client = LSReadOnlyClient(RuntimeSettings.from_env(env))
    with pytest.raises(RuntimeError, match="NO_EXTRA_FEE_CONFIRMED"):
        client.require_entitlement("kr_foreign")
    with pytest.raises(RuntimeError, match="NO_EXTRA_FEE_CONFIRMED"):
        client.require_entitlement("overseas_delayed")


def test_ls_delay_and_native_foreign_units_survive_parquet(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\n", encoding="utf-8")
    settings = RuntimeSettings.from_env(env)
    store = RuntimeStore(settings, tmp_path / "state.sqlite3")
    writer = ParquetBatchWriter(settings, store)
    flow = parse_dbt_foreign({"header": {"tr_cd": "DBT"}, "body": {
        "msvolume2": "5", "mdvolume2": "2", "msvol2": "3",
        "msvalue2": "7", "mdvalue2": "4", "msval2": "3",
    }}, "DFK2I", "2026-09-28")
    assert flow is not None and writer.add(flow)
    now = int(datetime(2026, 9, 28, 10, 15, tzinfo=KST).timestamp() * 1e9)
    quote = parse_delayed_future_trade(
        {"Symbol": "NQZ26", "KorDate": "20260928", "RcvTm": "100000", "TrdP": "21000", "TrdQ": "1"},
        "NQ", {"Symbol": "NQZ26", "BscGdsCd": "NQ"}, now,
    )
    assert quote is not None and writer.add(quote)
    rows = [row for path in writer.flush_all() for row in pq.ParquetFile(path).read().to_pylist()]
    by_type = {row["event_type"]: row for row in rows}
    assert by_type["participant_flow"]["value_unit"] == "LS_native_unverified"
    assert by_type["participant_flow"]["raw_payload"] is not None
    assert by_type["trade"]["observed_delay_ms"] == 900_000
    store.close()
