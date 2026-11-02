"""Read-only operational checks used by the bounded DEP-01 session."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import time

from execution.process_lease import ProcessLease
from runtime.runtime_paths import validate_runtime_paths
from storage.market_spool import scan_spool, read_spool_records, SpoolCursor


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=False, default=str).encode()).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _db(path):
    path = Path(path)
    if (not path.is_absolute() or ".." in path.parts or "\\" in str(path) or "\x00" in str(path) or
            path.is_symlink() or not path.is_file() or
            any(part.is_symlink() for part in (path.absolute(), *path.absolute().parents)) or
            path.stat().st_size > 1024 * 1024 * 1024):
        raise ValueError("database path is not a regular file")
    uri = path.resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=2)
    db.execute("PRAGMA query_only=ON")
    return db


def _scope_identity(value):
    try:
        parts = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        raise ValueError("account scope identity malformed") from None
    if (not isinstance(parts, list) or len(parts) != 4 or
            any(not isinstance(item, str) or not item for item in parts)):
        raise ValueError("account scope identity malformed")
    return parts


def account_facts_connection(db, scope):
    integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    meta = dict(db.execute("SELECT key,value FROM owner_metadata WHERE key IN ('scope','schema_version','account_version')"))
    if integrity != "ok" or meta.get("scope") != scope:
        raise ValueError("account ledger integrity/scope mismatch")
    facts = {}
    for table, order in (("execution_intents", "intent_id"),
                         ("execution_fills", "fill_id"),
                         ("execution_positions", "instrument")):
        rows = [list(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY {order} LIMIT 100001")]
        if len(rows) > 100000:
            raise ValueError("account fact row limit exceeded")
        facts[table] = {"count": len(rows), "sha256": digest(rows)}
    unknown = int(db.execute("SELECT COUNT(*) FROM execution_intents WHERE state IN ('AUTHORIZED','HANDOFF','UNKNOWN','LEGACY_UNKNOWN','SUBMITTED')").fetchone()[0])
    return {"meta": meta, "facts": facts, "unknown_count": unknown}


def account_facts(path, scope):
    db = _db(path)
    try:
        db.execute("BEGIN")
        return account_facts_connection(db, scope)
    finally:
        if db.in_transaction:
            db.rollback()
        db.close()


def paper_facts(path):
    db = _db(path)
    try:
        db.execute("BEGIN")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("PAPER ledger integrity failure")
        metadata = dict(db.execute("SELECT key,value FROM portfolio_metadata WHERE key IN ('mode','scope','account_version')"))
        if metadata.get("mode") != "paper" or not metadata.get("scope"):
            raise ValueError("PAPER ledger mode/scope mismatch")
        raw_events = [list(row) for row in db.execute("SELECT id,fingerprint,document FROM portfolio_receipts WHERE kind='strategy_event' ORDER BY id LIMIT 100001")]
        events = []
        for row in raw_events:
            try:
                document = json.loads(row[2])
            except (TypeError, json.JSONDecodeError):
                raise ValueError("PAPER strategy event receipt malformed") from None
            if document.get("event_id") != "@registration":
                events.append(row)
        fills = [list(row) for row in db.execute("SELECT id,fingerprint,document FROM portfolio_receipts WHERE kind='fill' ORDER BY id LIMIT 100001")]
        orders = [list(row) for row in db.execute("SELECT intent_id,state,filled,quantity FROM portfolio_orders ORDER BY intent_id LIMIT 100001")]
        if max(len(events), len(fills), len(orders)) > 100000:
            raise ValueError("PAPER ledger row limit exceeded")
        return {"metadata": metadata, "events": {"count": len(events), "sha256": digest(events)},
                "fills": {"count": len(fills), "sha256": digest(fills)},
                "orders": {"count": len(orders), "sha256": digest(orders)}}
    finally:
        if db.in_transaction:
            db.rollback()
        db.close()


def validate_payloads(artifacts, *, now_ns, session_id, source_sha256, manifest_sha256,
                      expected_schema=4, max_age_ns=300_000_000_000):
    """Validate nine group payloads and return group status/reason/data roots."""
    results = {}
    roots = []
    p = {group: item["payload"] for group, item in artifacts.items()}
    shapes = {
        "mounts": {"contract_path", "settings_paths"},
        "disk": {"path", "reserve_bytes", "reserve_inodes"},
        "owner": {"authority_root", "venue", "physical_account", "environment", "lease_path"},
        "sqlite": {"source_path", "backup_path", "backup_sha256", "scope", "schema_version", "account_version"},
        "latest_truth": {"source_path", "scope", "account_version", "unknown_count", "orders_sha256", "fills_sha256", "positions_sha256"},
        "spool": {"root", "producer_id", "incarnation", "limits", "expected_durable_sequence", "expected_records_sha256", "cloud_readback"},
        "kill_switch": {"source_path", "scope", "strategy_id", "expected_epochs"},
        "health": {"artifact_path", "artifact_sha256"},
        "paper_data": {"artifact_path", "artifact_sha256"},
    }
    for group, fields in shapes.items():
        if not isinstance(p.get(group), dict) or set(p[group]) != fields:
            raise ValueError("operational payload schema invalid: " + group)
    if set(p["mounts"]["settings_paths"]) != {"data_root", "state_dir", "parquet_root"}:
        raise ValueError("mount settings path inventory invalid")
    if set(p["spool"]["limits"]) != {"max_disk_bytes", "max_records", "max_frame_bytes", "max_header_bytes"}:
        raise ValueError("spool limit inventory invalid")
    cloud_ref = p["spool"]["cloud_readback"]
    if cloud_ref not in (None, "EXTERNAL_ONLY") and not (
            isinstance(cloud_ref, dict) and set(cloud_ref) == {"evidence_class", "path", "sha256"} and
            cloud_ref.get("evidence_class") == "LOCAL_FAKE"):
        raise ValueError("cloud readback classification invalid")
    # Mount identity is established first; no data path is opened before it.
    mount = p["mounts"]
    settings_paths = mount["settings_paths"]
    contract = Path(mount["contract_path"])
    if (not contract.is_absolute() or ".." in contract.parts or "\\" in str(contract) or
            any(part.is_symlink() for part in (contract.absolute(), *contract.absolute().parents)) or
            contract.is_symlink() or not contract.is_file() or contract.stat().st_size > 1024 * 1024):
        raise ValueError("mount contract is missing or unsafe")
    contract_doc = json.loads(contract.read_text(encoding="utf-8"))
    roots = [Path(row["path"]).resolve(strict=True) for row in contract_doc["mounts"]]
    fake_env = {"VIBETRADING_ENFORCE_MOUNTS": "true",
                "VIBETRADING_STORAGE_MOUNTS_FILE": str(contract)}
    settings = SimpleNamespace(env=fake_env, data_root=Path(settings_paths["data_root"]),
        state_dir=Path(settings_paths["state_dir"]),
        parquet=SimpleNamespace(data_root=Path(settings_paths["parquet_root"])))
    verified = validate_runtime_paths(settings)
    roots = [Path(row["path"]) for row in verified]
    def in_mount(path):
        raw = Path(path)
        if (not raw.is_absolute() or ".." in raw.parts or "\\" in str(raw) or "\x00" in str(raw) or
                any(part.is_symlink() for part in (raw.absolute(), *raw.absolute().parents))):
            raise ValueError("operational path is noncanonical or contains symlink")
        resolved = raw.resolve(strict=True)
        if not any(resolved.is_relative_to(root) for root in roots):
            raise ValueError("operational path outside verified mounts")
        return resolved
    results["mounts"] = {"status": "PASS", "verified": verified}

    disk = p["disk"]
    target = in_mount(disk["path"])
    if any(type(disk[k]) is not int or disk[k] <= 0 for k in ("reserve_bytes", "reserve_inodes")):
        raise ValueError("disk reserves must be positive integers")
    stat = os.statvfs(target)
    free_bytes, free_inodes = stat.f_bavail * stat.f_frsize, stat.f_favail
    if free_bytes < disk["reserve_bytes"] or free_inodes < disk["reserve_inodes"]:
        raise ValueError("disk reserve insufficient")
    results["disk"] = {"status": "PASS", "free_bytes": free_bytes, "free_inodes": free_inodes}

    owner = p["owner"]
    authority = in_mount(owner["authority_root"])
    expected = ProcessLease.account_for_route(authority, owner["venue"],
        owner["physical_account"], owner["environment"]).path
    lease = Path(owner["lease_path"])
    if lease != expected or lease.is_symlink() or not lease.is_file():
        raise ValueError("cooperative owner lease identity missing/mismatch")
    in_mount(lease)
    fd = os.open(lease, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd); current = lease.stat(follow_symlinks=False)
        if not os.path.isfile(lease) or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError("owner lease changed during probe")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("cooperating account owner is active") from None
        fcntl.flock(fd, fcntl.LOCK_UN)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError("owner lease replaced during probe")
    finally:
        os.close(fd)
    results["owner"] = {"status": "PASS", "lease_path": str(lease),
                        "noncooperating_owners": "EXTERNAL_ONLY"}

    sql = p["sqlite"]
    source = in_mount(sql["source_path"]); backup = in_mount(sql["backup_path"])
    scope_parts = _scope_identity(sql["scope"])
    if (scope_parts[0] != owner["venue"] or scope_parts[1] != owner["physical_account"] or
            scope_parts[3] != owner["environment"]):
        raise ValueError("owner and execution account scopes differ")
    if sql["schema_version"] != expected_schema:
        raise ValueError("execution ledger schema differs from frozen release")
    if not backup.is_file() or backup.is_symlink() or sha_file(backup) != sql["backup_sha256"]:
        raise ValueError("SQLite backup hash mismatch")
    live = account_facts(source, sql["scope"]); snap = account_facts(backup, sql["scope"])
    if int(live["meta"]["schema_version"]) != sql["schema_version"] or int(live["meta"]["account_version"]) != sql["account_version"]:
        raise ValueError("live ledger schema/account cut mismatch")
    if live != snap:
        raise ValueError("SQLite backup does not match current WAL/account facts")
    if int(live["meta"]["schema_version"]) != sql["schema_version"]:
        raise ValueError("SQLite schema version mismatch")
    results["sqlite"] = {"status": "PASS", "account_version": sql["account_version"], "facts": live["facts"]}

    truth = p["latest_truth"]
    truth_source = in_mount(truth["source_path"])
    if truth_source != source or truth["scope"] != sql["scope"] or truth["account_version"] != sql["account_version"]:
        raise ValueError("latest truth is not bound to the verified SQLite account cut")
    current = account_facts(truth_source, truth["scope"])
    observed = int(artifacts["latest_truth"]["observed_ns"])
    age = now_ns - observed
    if age < 0 or age > max_age_ns:
        raise ValueError("latest account truth is stale or future-dated")
    if (int(current["meta"]["account_version"]) != truth["account_version"] or
        current["unknown_count"] != truth["unknown_count"] or
        current["facts"]["execution_intents"]["sha256"] != truth["orders_sha256"] or
        current["facts"]["execution_fills"]["sha256"] != truth["fills_sha256"] or
        current["facts"]["execution_positions"]["sha256"] != truth["positions_sha256"]):
        raise ValueError("latest truth facts changed")
    if current["unknown_count"]:
        raise ValueError("unresolved UNKNOWN/order state blocks mutation")
    results["latest_truth"] = {"status": "PASS", "account_version": truth["account_version"], "age_ns": age}

    spool = p["spool"]
    limits = spool["limits"]
    spool_root = in_mount(spool["root"])
    report = scan_spool(spool_root, producer_id=spool["producer_id"], incarnation=spool["incarnation"], **limits)
    records = read_spool_records(spool_root, producer_id=spool["producer_id"],
        incarnation=spool["incarnation"], **limits, through=SpoolCursor(spool["producer_id"],
        spool["incarnation"], report.durable_sequence))
    chain = hashlib.sha256()
    record_count = 0
    for item in records:
        chain.update(item.cursor.sequence.to_bytes(8, "big")); chain.update(item.payload)
        record_count += 1
    if (report.durable_sequence != spool["expected_durable_sequence"] or
            record_count != report.durable_sequence or chain.hexdigest() != spool["expected_records_sha256"]):
        raise ValueError("spool durable prefix mismatch")
    cloud = spool["cloud_readback"]
    cloud_state = "EXTERNAL_ONLY"
    if isinstance(cloud, dict) and cloud.get("evidence_class") == "LOCAL_FAKE":
        path = in_mount(cloud["path"])
        if path.is_symlink() or not path.is_file() or sha_file(path) != cloud["sha256"]:
            raise ValueError("local spool readback artifact mismatch")
        cloud_state = "LOCAL_FAKE_READBACK"
    results["spool"] = {"status": "PASS", "durable_sequence": report.durable_sequence,
                        "records_sha256": chain.hexdigest(), "cloud": cloud_state}

    kill = p["kill_switch"]
    kill_source = in_mount(kill["source_path"])
    if kill_source != source or kill["scope"] != "account:" + owner["physical_account"]:
        raise ValueError("kill switch belongs to another account ledger")
    db = _db(kill_source)
    try:
        rows = {row[0]: (int(row[1]), int(row[2])) for row in db.execute(
            "SELECT scope,epoch,closed FROM execution_controls")}
    finally:
        db.close()
    epochs = kill["expected_epochs"]
    expected_control_scopes = {"global", kill["scope"], kill["strategy_id"]}
    if (not isinstance(epochs, dict) or set(epochs) != expected_control_scopes or any(
        type(epoch) is not int or scope not in rows or rows[scope] != (epoch, 1)
        for scope, epoch in epochs.items())):
        raise ValueError("kill-switch control rows are not closed at expected epochs")
    results["kill_switch"] = {"status": "PASS", "scopes": sorted(epochs)}

    for group in ("health", "paper_data"):
        payload = p[group]
        path = Path(payload["artifact_path"])
        if (not path.is_absolute() or ".." in path.parts or path.is_symlink() or
                any(part.is_symlink() for part in (path.absolute(), *path.absolute().parents)) or
                not path.is_file() or path.stat().st_size > 8 * 1024 * 1024 or
                sha_file(path) != payload["artifact_sha256"]):
            raise ValueError(group + " proof artifact hash mismatch")
        proof = json.loads(path.read_text(encoding="utf-8"))
        if (proof.get("evidence_class") != "LOCAL_FAKE" or proof.get("session_id") != session_id or
            proof.get("source_sha256") != source_sha256 or proof.get("manifest_sha256") != manifest_sha256 or
            proof.get("observed_ns") != artifacts[group]["observed_ns"]):
            raise ValueError(group + " artifact lineage mismatch")
        if group == "health":
            if (proof.get("roles_started") is not False or proof.get("telegram_transport") != "FAKE_CLOSED" or
                    not isinstance(proof.get("cold_roles"), list) or not proof["cold_roles"]):
                raise ValueError("health proof is not a cold local role result")
            if proof.get("spool_path") != str(spool_root) or proof.get("spool_sha256") != chain.hexdigest():
                raise ValueError("health proof does not bind scanned spool facts")
        else:
            if not (isinstance(proof.get("durable_cursor"), int) and proof["durable_cursor"] > 0 and
                    isinstance(proof.get("strategy_callbacks"), int) and proof["strategy_callbacks"] > 0 and
                    isinstance(proof.get("paper_fills"), int) and proof["paper_fills"] >= 0 and
                    proof.get("engine_class") == "LocalMarketEngine" and
                    proof.get("paper_runtime_class") == "PaperStrategyRuntime"):
                raise ValueError("PAPER/data proof lacks actual engine/runtime facts")
            if (proof.get("durable_cursor") != report.durable_sequence or
                    proof.get("spool_path") != str(spool_root) or proof.get("spool_sha256") != chain.hexdigest()):
                raise ValueError("PAPER proof does not bind scanned durable spool facts")
            ledger_path = in_mount(proof.get("ledger_path", ""))
            actual = paper_facts(ledger_path)
            if proof.get("ledger_facts_sha256") != digest(actual):
                raise ValueError("PAPER proof ledger fact digest mismatch")
            if (proof["strategy_callbacks"] > actual["events"]["count"] or
                    proof["paper_fills"] != actual["fills"]["count"]):
                raise ValueError("PAPER callback/fill claims exceed durable ledger receipts")
        results[group] = {"status": "PASS", "artifact_sha256": payload["artifact_sha256"]}
    return {"results": results, "verified_roots": [str(item) for item in roots]}
