from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq

from vibetrading.bus import InMemoryEventBus
from core.models import EventType
from vibetrading.parquet_store import ParquetBatchWriter
from vibetrading.providers.kis import kis_participant_flow_events
from vibetrading.settings import RuntimeSettings
from vibetrading.sqlite_store import RuntimeStore


def test_kis_participant_flow_preserves_buy_sell_and_net_values(tmp_path: Path) -> None:
    rows = [{
        "prsn_shnu_vol": "100", "prsn_seln_vol": "30", "prsn_ntby_qty": "70",
        "prsn_shnu_tr_pbmn": "1000", "prsn_seln_tr_pbmn": "300", "prsn_ntby_tr_pbmn": "700",
        "frgn_shnu_vol": "20", "frgn_seln_vol": "50",
        "frgn_shnu_tr_pbmn": "200", "frgn_seln_tr_pbmn": "500",
    }]
    events = kis_participant_flow_events(
        rows, market_code="K2I", market_subcode="0001", wallclock_ts_ns=120_000_000_000,
        collector_run_id="test", collection_start_ts_ns=60_000_000_000,
    )
    assert {event.participant for event in events} == {"prsn", "frgn"}
    foreign = next(event for event in events if event.participant == "frgn")
    assert foreign.net_volume == Decimal("-30")
    assert foreign.net_value == Decimal("-300")
    assert foreign.source_ts_ns is None
    assert foreign.event_type == EventType.PARTICIPANT_FLOW

    env = tmp_path / ".env"
    env.write_text(f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\n", encoding="utf-8")
    settings = RuntimeSettings.from_env(env)
    writer = ParquetBatchWriter(settings, RuntimeStore(settings, tmp_path / "state.sqlite3"))
    for event in events:
        assert writer.add(event)
    path = writer.flush_all()[0]
    stored = pq.ParquetFile(path).read().to_pylist()
    assert stored[0]["event_type"] == "participant_flow"
    assert stored[0]["source_ts_ns"] is None


def test_in_memory_bus_accepts_kis_participant_flow() -> None:
    event = kis_participant_flow_events(
        [{"prsn_shnu_vol": "1", "prsn_seln_vol": "0", "prsn_shnu_tr_pbmn": "1", "prsn_seln_tr_pbmn": "0"}],
        market_code="K2I", market_subcode="0001", wallclock_ts_ns=60_000_000_000,
        collector_run_id="test", collection_start_ts_ns=0,
    )[0]
    import asyncio
    bus = InMemoryEventBus()
    asyncio.run(bus.publish_market_event(event))
    assert len(bus.messages) == 1
