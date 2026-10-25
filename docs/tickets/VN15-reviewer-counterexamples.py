"""Codex independent counterexamples: cold reads must not claim owner freshness."""
import asyncio
import json
from decimal import Decimal as D
import pytest
from test_strategy_runtime import ledger_at
from vibetrading.dashboard.projection import paper_projection

@pytest.mark.asyncio
async def test_real_owned_paper_db_read_from_foreign_thread_preserves_all_facts(tmp_path):
    ledger=ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT",D("1000.000000000000000001"),{"source":"synthetic"})
        before=tuple(ledger._db.iterdump())
        result=await asyncio.to_thread(paper_projection,[ledger.path],limit=20)
        assert result["mode"]=="paper" and result["evidence"]=="PAPER_SIMULATION"
        assert result["freshness"]=="UNVERIFIED" and result["fact_asof_ns"] is None
        assert len(result["accounts"])==1 and not result["errors"]
        assert tuple(ledger._db.iterdump())==before
    finally:
        ledger.close()

def test_missing_db_is_visible_unavailable_and_does_not_create_database(tmp_path):
    path=tmp_path/"absent.sqlite3"
    result=paper_projection([path],limit=20)
    assert result["errors"] and not result["accounts"]
    assert "UNAVAILABLE" in json.dumps(result)
    assert not path.exists()

def test_real_mode_metadata_never_becomes_a_paper_account(tmp_path):
    ledger=ledger_at(tmp_path)
    try:
        ledger._db.execute("UPDATE portfolio_metadata SET value='real' WHERE key='mode'")
        before=tuple(ledger._db.iterdump())
        result=paper_projection([ledger.path],limit=20)
        assert result["errors"] and not result["accounts"]
        assert tuple(ledger._db.iterdump())==before
    finally:
        ledger.close()

def test_projection_identity_and_position_fields_match_actual_facts(tmp_path):
    from test_strategy_runtime import (matcher_for,runtime,registration,deliver,book,trade,T,BTC)
    from vibetrading.models import Side
    ledger=ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT",D(100000),{"source":"synthetic"})
        matcher=matcher_for(ledger);now=[T];host=runtime(ledger,matcher,now)
        host.register(registration(ledger,"alpha",[]))
        deliver(host,now,book())
        deliver(host,now,trade(T+11,side=Side.SELL,price=99).model_copy(update={"quantity":D(6)}))
        result=paper_projection([ledger.path],limit=20)
        assert not result["errors"] and result["positions"]
        for row in result["positions"]:
            assert row["venue"]==BTC.venue.value
            assert row["asset_class"]==BTC.asset_class.value
            assert row["symbol"]==BTC.std_symbol
        for row in result["orders"]:
            assert row["intent_id"] and row["id"]!=row["intent_id"]
        assert all(row["id"] and row["name"]==row["strategy"] for row in result["strategies"])
    finally:
        ledger.close()
