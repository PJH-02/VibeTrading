"""Deterministic LOCAL_FAKE helpers for DEP-01 unit tests only."""
import hashlib
import json
from pathlib import Path
import time

from scripts.refactoring.deployment_session import _seal


def make_paper_proof(path, *, session_id, source_sha256, manifest_sha256,
                     engine, paper_runtime, ledger, strategy_id,
                     spool_root, producer_id, incarnation, limits, observed_ns=None):
    """Persist a proof derived from real runtime/ledger/spool objects, never caller counters."""
    from market.local_engine import LocalMarketEngine
    from runtime.strategy_runtime import PaperStrategyRuntime
    from scripts.refactoring.deployment_checks import digest, paper_facts
    from storage.market_spool import scan_spool, read_spool_records, SpoolCursor
    if not isinstance(engine, LocalMarketEngine) or not isinstance(paper_runtime, PaperStrategyRuntime):
        raise TypeError("fixture requires the real local engine and PAPER runtime")
    observed_ns = int(time.time_ns() if observed_ns is None else observed_ns)
    report = paper_runtime.report()
    callbacks = report["strategies"][strategy_id]["callbacks"]
    facts = paper_facts(ledger.path)
    scan = scan_spool(Path(spool_root), producer_id=producer_id, incarnation=incarnation, **limits)
    digest_state = hashlib.sha256()
    count = 0
    for row in read_spool_records(Path(spool_root), producer_id=producer_id,
            incarnation=incarnation, **limits,
            through=SpoolCursor(producer_id, incarnation, scan.durable_sequence)):
        digest_state.update(row.cursor.sequence.to_bytes(8, "big")); digest_state.update(row.payload)
        count += 1
    if count != scan.durable_sequence:
        raise ValueError("fixture spool did not reopen at its durable cursor")
    document = {"evidence_class": "LOCAL_FAKE", "session_id": session_id,
        "source_sha256": source_sha256, "manifest_sha256": manifest_sha256,
        "observed_ns": observed_ns, "durable_cursor": scan.durable_sequence,
        "strategy_callbacks": callbacks, "paper_fills": facts["fills"]["count"],
        "engine_class": type(engine).__name__, "paper_runtime_class": type(paper_runtime).__name__,
        "spool_path": str(Path(spool_root).resolve()), "spool_sha256": digest_state.hexdigest(),
        "ledger_path": str(Path(ledger.path).resolve()), "ledger_facts_sha256": digest(facts)}
    path = Path(path)
    content_path = path.with_name(path.stem + ".runtime.json")
    content_path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    content_sha = hashlib.sha256(content_path.read_bytes()).hexdigest()
    envelope = {"evidence_class": "LOCAL_FAKE", "session_id": session_id,
        "source_sha256": source_sha256, "manifest_sha256": manifest_sha256,
        "observed_ns": observed_ns, "group": "paper_data",
        "payload": {"artifact_path": str(content_path.resolve()), "artifact_sha256": content_sha}}
    path.write_text(json.dumps(envelope, sort_keys=True) + "\n", encoding="utf-8")
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def make_host_proofs(root, manifest, *, now_ns, session_id, source_sha256,
                     manifest_sha256, contract_path, settings_paths, reserve_bytes,
                     reserve_inodes, execution_ledger, authority_root, venue,
                     physical_account, environment, strategy_id, account_version, spool_root,
                     producer_id, incarnation, limits, health_artifact,
                     paper_artifact, precreated_snapshot=None):
    """Build seven operational payloads from live local objects and attach two real run reports.

    `health_artifact` and `paper_artifact` must already be produced by exercised
    local-runtime fixtures; this helper refuses to synthesize those successes.
    """
    import sqlite3
    from execution.process_lease import ProcessLease
    from scripts.refactoring.deployment_checks import account_facts
    from scripts.refactoring.deployment_session import snapshot_sqlite
    from storage.market_spool import scan_spool, read_spool_records, SpoolCursor

    root = Path(root).resolve(strict=True)
    if not getattr(execution_ledger, "_closed", False) or type(account_version) is not int:
        raise ValueError("close the real execution owner and pass its captured version before evidence capture")
    evidence = root / "dep01-proof-artifacts"
    evidence.mkdir(mode=0o700, exist_ok=True)
    artifacts = {}

    def put(group, payload):
        path = evidence / (group + ".json")
        envelope = {"evidence_class": "LOCAL_FAKE", "observed_ns": now_ns,
            "source_sha256": source_sha256, "manifest_sha256": manifest_sha256,
            "session_id": session_id, "group": group, "payload": payload}
        path.write_text(json.dumps(envelope, sort_keys=True) + "\n", encoding="utf-8")
        artifacts[group] = {"path": str(path.resolve()),
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    put("mounts", {"contract_path": str(Path(contract_path).resolve()),
                    "settings_paths": {k: str(Path(v).resolve()) for k, v in settings_paths.items()}})
    put("disk", {"path": str(root), "reserve_bytes": reserve_bytes, "reserve_inodes": reserve_inodes})
    owner = ProcessLease.account_for_route(Path(authority_root).resolve(), venue, physical_account, environment)
    put("owner", {"authority_root": str(Path(authority_root).resolve()), "venue": venue,
        "physical_account": physical_account, "environment": environment,
        "lease_path": str(owner.path)})
    source_db = Path(execution_ledger.path)
    if precreated_snapshot is None:
        snapshot_path = evidence / "execution-ledger-backup.sqlite"
        snapshot = snapshot_sqlite(source_db, snapshot_path, expected_scope=execution_ledger.scope.key,
            expected_schema=execution_ledger.SCHEMA_VERSION,
            expected_account_version=account_version)
    else:
        snapshot_path = Path(precreated_snapshot["path"])
        if (not snapshot_path.is_file() or hashlib.sha256(snapshot_path.read_bytes()).hexdigest() !=
                precreated_snapshot["sha256"]):
            raise ValueError("precreated WAL snapshot hash mismatch")
        snapshot = {**precreated_snapshot, "sha256": precreated_snapshot["sha256"],
                    "schema_version": execution_ledger.SCHEMA_VERSION,
                    "account_version": account_version}
        if account_facts(source_db, execution_ledger.scope.key) != account_facts(snapshot_path, execution_ledger.scope.key):
            raise ValueError("precreated snapshot differs from closed latest ledger cut")
    put("sqlite", {"source_path": str(source_db.resolve()), "backup_path": str(snapshot_path.resolve()),
        "backup_sha256": snapshot["sha256"], "scope": execution_ledger.scope.key,
        "schema_version": snapshot["schema_version"], "account_version": snapshot["account_version"]})
    facts = account_facts(source_db, execution_ledger.scope.key)
    put("latest_truth", {"source_path": str(source_db.resolve()), "scope": execution_ledger.scope.key,
        "account_version": int(facts["meta"]["account_version"]),
        "unknown_count": facts["unknown_count"],
        "orders_sha256": facts["facts"]["execution_intents"]["sha256"],
        "fills_sha256": facts["facts"]["execution_fills"]["sha256"],
        "positions_sha256": facts["facts"]["execution_positions"]["sha256"]})
    scan = scan_spool(Path(spool_root), producer_id=producer_id, incarnation=incarnation, **limits)
    record_digest = hashlib.sha256(); count = 0
    for row in read_spool_records(Path(spool_root), producer_id=producer_id,
            incarnation=incarnation, **limits,
            through=SpoolCursor(producer_id, incarnation, scan.durable_sequence)):
        record_digest.update(row.cursor.sequence.to_bytes(8, "big")); record_digest.update(row.payload)
        count += 1
    if count != scan.durable_sequence:
        raise ValueError("fixture spool did not reopen at its durable cursor")
    put("spool", {"root": str(Path(spool_root).resolve()), "producer_id": producer_id,
        "incarnation": incarnation, "limits": limits,
        "expected_durable_sequence": scan.durable_sequence,
        "expected_records_sha256": record_digest.hexdigest(), "cloud_readback": "EXTERNAL_ONLY"})
    db = sqlite3.connect(source_db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        controls = {row[0]: int(row[1]) for row in db.execute(
            "SELECT scope,epoch FROM execution_controls WHERE closed=1")}
    finally:
        db.close()
    expected = {"global": controls["global"], "account:" + physical_account: controls["account:" + physical_account],
                "strategy:" + strategy_id: controls["strategy:" + strategy_id]}
    put("kill_switch", {"source_path": str(source_db.resolve()), "scope": "account:" + physical_account,
        "strategy_id": "strategy:" + strategy_id, "expected_epochs": expected})
    for group, ref in (("health", health_artifact), ("paper_data", paper_artifact)):
        path = Path(ref["path"]).resolve(strict=True)
        artifacts[group] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return {"evidence_class": "LOCAL_FAKE", "observed_ns": now_ns,
        "source_sha256": source_sha256, "manifest_sha256": manifest_sha256,
        "artifacts": artifacts}


def fake_session(release_root: Path, manifest_path: Path, *, p0=False):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    value = {"schema_version": 1, "session_id": "unit-session-01", "created_ns": 10,
             "frozen_sha": manifest["source_sha256"],
             "manifest_sha256": manifest["manifest_sha256"],
             "release_root": str(release_root),
             "evidence_root": str(release_root.parent),
             "lineage": {"manifest": {"path": str(manifest_path),
                                      "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()}},
             "roles": [], "gates": [], "deployment_state": "DEPLOY_BLOCKED",
             "p0_latched": p0, "install": {"state": "NOT_INSTALLED", "stage": None},
             "activation": "EXTERNAL_ONLY_CUT01_REQUIRED", "database_restore": "PROHIBITED"}
    value["session_sha256"] = _seal(value)
    return value


def fake_release(root: Path):
    root.mkdir()
    raw = b"safe frozen local fixture\n"
    (root / "app.py").write_bytes(raw)
    entry = {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
    body = {"files": {"app.py": entry}, "source_sha256": "a" * 64}
    body["manifest_sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    manifest = root.parent / "manifest.json"
    manifest.write_text(json.dumps(body), encoding="utf-8")
    return manifest
