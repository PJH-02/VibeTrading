from __future__ import annotations

import base64
import json
import sqlite3
import time
from decimal import Decimal

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from operations.dashboard.projection import MarketProjection, probe_orders, runtime_orders, timefolio_metrics
from operations.dashboard.timefolio_monitor import decrypt
from core.models import AssetClass, BookUpdateType, DataGapEvent, InstrumentId, OrderBookEvent, OrderBookLevel, TradeEvent, Venue


def common():
    return {"instrument": InstrumentId(venue=Venue.BYBIT, asset_class=AssetClass.CRYPTO_PERP,
                raw_symbol="SOLUSDT", std_symbol="SOLUSDT", currency="USDT"),
            "venue": Venue.BYBIT, "provider": "bybit-perp", "receive_ts_ns": time.time_ns()}


def book(kind, sequence, previous=None):
    return OrderBookEvent(**common(), update_type=kind, depth=1,
        bids=[OrderBookLevel(price=Decimal(100), quantity=Decimal(2))],
        asks=[OrderBookLevel(price=Decimal(101), quantity=Decimal(3))],
        final_update_id=sequence, previous_update_id=previous, sync_generation=1, sequence=sequence)


def test_projection_rejects_unseeded_and_gapped_book():
    projection = MarketProjection()
    projection.apply(book(BookUpdateType.DELTA, 2, 1).model_dump(mode="json"))
    assert projection.snapshot()[0]["book"] is None
    projection.apply(book(BookUpdateType.SNAPSHOT, 10).model_dump(mode="json"))
    assert projection.snapshot()[0]["book"]["bids"] == [["100", "2"]]
    projection.apply(book(BookUpdateType.DELTA, 12, 10).model_dump(mode="json"))
    assert projection.snapshot()[0]["book"] is None
    projection.apply(book(BookUpdateType.SNAPSHOT, 20).model_dump(mode="json"))
    projection.apply(DataGapEvent(**common(), reason="disconnect").model_dump(mode="json"))
    assert projection.snapshot()[0]["book"] is None


def test_trades_are_bounded_and_source_time_is_not_fabricated():
    projection = MarketProjection()
    for index in range(100):
        projection.apply(TradeEvent(**common(), price=Decimal(100), quantity=Decimal(1), trade_id=str(index)).model_dump(mode="json"))
    row = projection.snapshot()[0]
    assert len(row["trades"]) == 40
    assert row["trades"][0]["source_ms"] is None
    assert row["section"] == "crypto"
    assert row["currency"] == "USDT"


def test_timefolio_does_not_infer_realized_pnl_from_nav_or_holdings():
    summary = {"nav_text": "1,020,000", "tables": [{"rows": [["평가손익", "20,000"]]}]}
    assert timefolio_metrics(summary) == {"nav": None, "realized_pnl": None, "turnover_pct": None}
    summary["tables"].append({"rows": [["회전율", "125.3%"], ["실현손익", "-2,450원"], ["순자산총액", "1,020,000원"]]})
    assert timefolio_metrics(summary)["realized_pnl"] == "-2450"
    assert timefolio_metrics(summary)["turnover_pct"] == "125.3"
    assert timefolio_metrics(summary)["nav"] == "1020000"


def test_account_credentials_roundtrip_hybrid_encryption():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    secret, iv = AESGCM.generate_key(bit_length=256), b"123456789012"
    credentials = {"username": "test", "password": "a long password" * 80}
    encode = lambda data: base64.b64encode(data).decode()
    envelope = {"wrapped_key": encode(key.public_key().encrypt(secret, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))),
                "iv": encode(iv), "ciphertext": encode(AESGCM(secret).encrypt(iv, json.dumps(credentials).encode(), None))}
    assert decrypt(envelope, key) == credentials
    assert credentials["password"] not in json.dumps(envelope)


def test_runtime_order_projection_is_read_only_and_allowlisted(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE order_intents(intent_id TEXT, job_id TEXT, signal_id TEXT, destination TEXT, client_order_id TEXT, status TEXT, payload TEXT, created_ts_ns INTEGER, updated_ts_ns INTEGER)")
        payload = {"instrument": {"asset_class": "equity", "std_symbol": "005930", "venue": "kis"},
                   "metadata": {"account_id": "account-1", "strategy_id": "strategy-1"}, "side": "buy", "password": "must-not-leak", "broker_response": {"token": "must-not-leak"}}
        db.execute("INSERT INTO order_intents VALUES(?,?,?,?,?,?,?,?,?)", ("intent-1", "job-1", "signal-1", "real", "client-1", "submitted", json.dumps(payload), 123_000_000, 124_000_000))
    before = path.read_bytes()
    rows = runtime_orders([path])
    assert path.read_bytes() == before
    assert "must-not-leak" not in json.dumps(rows)
    assert rows[0]["status"] == "submitted"
    assert rows[0]["account"] == "account-1"
    assert rows[0]["mode"] == "unverified"


def test_preserved_pilot_orders_convert_monotonic_time_to_wall_time(tmp_path):
    from execution.bybit_execution import BybitAudit
    audit = BybitAudit(tmp_path / "bybit_test.sqlite3")
    audit.create("client-1", "SOLUSDT", "Buy", "maker", "0.1", "100", 1_000_000_000)
    audit.transition("client-1", "FILLED", timestamp=1_200_000_000, order_id="exchange-1")
    first_wall = audit.db.execute("SELECT wall_ns FROM transitions").fetchone()[0]
    audit.close()
    orders = probe_orders(tmp_path)
    assert orders[0]["created_ms"] == (first_wall - 200_000_000) // 1_000_000
    assert orders[0]["id"] == "bybit-testnet:exchange-1"
    assert orders[0]["mode"] == "testnet"
    assert orders[0]["status"] == "filled"
