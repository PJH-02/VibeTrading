"""DEP-01 bounded session, sandbox, SQLite, and forbidden-action tests."""
import hashlib
import sqlite3
from pathlib import Path

import pytest

from scripts.refactoring.deployment_session import (
    LOCAL_MARKER, _validated_entries, latch_p0, run_mode, snapshot_sqlite,
)
from dep01_session_fixture import fake_release, fake_session, make_paper_proof
from execution.process_lease import ProcessLease
from scripts.refactoring import release_candidate as rc
from test_rel01_release import repo as release_repo, _make_wheel
from scripts.refactoring.deployment_session import create_session
from scripts.refactoring.deployment_session import validate_import_origins
from scripts.refactoring.deployment_checks import validate_payloads


def test_modes_are_read_only_and_activation_always_external(tmp_path):
    release = tmp_path / "release"
    manifest = fake_release(release)
    session = fake_session(release, manifest)
    assert run_mode(session, "plan")["session_id"] == session["session_id"]
    assert run_mode(session, "activate")["activation"] == "BLOCKED"
    for mode in ("preflight", "verify"):
        with pytest.raises(ValueError, match="operational preflight failed"):
            run_mode(session, mode)
    assert run_mode(session, "rollback")["rollback"] == "P0_BLOCKED"


def test_real_rel_candidate_is_revalidated_and_missing_host_proof_blocks(tmp_path):
    root = release_repo(tmp_path)
    wheel = tmp_path / "candidate.whl"
    wheel_sha = _make_wheel(root, wheel)
    manifest = rc.freeze(root, wheel={"path": str(wheel), "expected_sha256": wheel_sha})
    manifest_path = tmp_path / "release.json"
    manifest_path.write_text(__import__("json").dumps(manifest), encoding="utf-8")
    session = create_session(manifest_path, manifest["manifest_sha256"], root,
        tmp_path, wheel_path=wheel, expected_wheel_sha256=wheel_sha)
    with pytest.raises(ValueError, match="operational preflight failed"):
        run_mode(session, "preflight")
    assert (tmp_path / ("P0-" + session["manifest_sha256"])).is_file()
    mixed = dict(session, p0_latched=False)
    from scripts.refactoring.deployment_session import _seal
    mixed["session_sha256"] = _seal(mixed)
    assert run_mode(mixed, "plan")["p0_latched"] is True
    assert run_mode(mixed, "rollback")["rollback"] == "P0_BLOCKED"
    with pytest.raises(ValueError, match="operational preflight failed"):
        run_mode(mixed, "preflight")
    fresh = create_session(manifest_path, manifest["manifest_sha256"], root,
        tmp_path, wheel_path=wheel, expected_wheel_sha256=wheel_sha)
    target = root / "config/runtime.yaml"
    target.write_text("safe: changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="operational preflight failed"):
        run_mode(fresh, "preflight")
    assert (tmp_path / ("P0-" + fresh["manifest_sha256"])).is_file()


def test_ignored_root_strategies_shadow_is_rejected(tmp_path):
    root = release_repo(tmp_path)
    shadow = root / "strategies"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("SHADOW = True\n", encoding="utf-8")
    exclude = root / ".git/info/exclude"
    with exclude.open("a", encoding="utf-8") as stream:
        stream.write("\n/strategies/\n")
    # The ignored file leaves the candidate's tracked Git manifest unchanged,
    # but Python's actual origin from the repository root still resolves here.
    with pytest.raises(ValueError, match="canonical source import shadow"):
        validate_import_origins(root)


def test_session_tamper_and_p0_latch_block_stage(tmp_path):
    release = tmp_path / "release"
    manifest = fake_release(release)
    session = fake_session(release, manifest)
    changed = dict(session, session_id="other-session")
    with pytest.raises(ValueError, match="seal"):
        run_mode(changed, "plan")
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / ".DEP01-LOCAL-FAKE").write_text(LOCAL_MARKER, encoding="utf-8")
    latched = latch_p0(session, "fixture fault")
    with pytest.raises(ValueError, match="P0"):
        run_mode(latched, "install-blocked", sandbox_root=sandbox,
                 local_fake_marker=LOCAL_MARKER)
    assert list(sandbox.iterdir()) == [sandbox / ".DEP01-LOCAL-FAKE"]


def test_operational_gate_rejects_unbound_success_flags_without_opening_paths():
    groups = ("mounts", "owner", "sqlite", "latest_truth", "disk", "spool",
              "health", "paper_data", "kill_switch")
    artifacts = {name: {"payload": {"status": "PASS"}, "observed_ns": 1} for name in groups}
    with pytest.raises(ValueError, match="payload schema invalid: mounts"):
        validate_payloads(artifacts, now_ns=1, session_id="x", source_sha256="a" * 64,
                          manifest_sha256="b" * 64)


def test_install_refuses_missing_operational_proof_before_mutation(tmp_path):
    release = tmp_path / "release"
    manifest = fake_release(release)
    _ = fake_session(release, manifest)
    with pytest.raises(ValueError, match="operational preflight failed"):
        run_mode(fake_session(release, manifest), "install-blocked",
                 sandbox_root=tmp_path, local_fake_marker=LOCAL_MARKER)


@pytest.mark.parametrize("bad", ["../escape", "/absolute"])
def test_install_rejects_path_traversal_before_mutation(tmp_path, bad):
    release = tmp_path / "release"
    manifest = fake_release(release)
    import json
    body = json.loads(manifest.read_text())
    body["files"][bad] = {"sha256": "0" * 64, "size": 1}
    manifest.write_text(json.dumps(body))
    with pytest.raises(ValueError, match="unsafe"):
        _validated_entries(release, body["files"])


def test_install_rejects_release_source_change_before_creating_stage(tmp_path):
    release = tmp_path / "release"
    manifest = fake_release(release)
    (release / "app.py").write_text("changed after freeze")
    with pytest.raises(ValueError, match="source changed"):
        import json
        _validated_entries(release, json.loads(manifest.read_text())["files"])


def test_process_lease_fences_a_second_cooperating_account_owner(tmp_path):
    root = tmp_path / "leases"
    root.mkdir()
    owner = ProcessLease.account(root, "kis", "physical-acct", "paper")
    owner.acquire()
    try:
        contender = ProcessLease.account(root, "kis", "physical-acct", "paper")
        with pytest.raises(RuntimeError, match="another process owns"):
            contender.acquire()
        owner.check()
    finally:
        owner.close()
    next_owner = ProcessLease.account(root, "kis", "physical-acct", "paper")
    next_owner.acquire()
    next_owner.close()


def _db(path):
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE owner_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
      CREATE TABLE execution_intents(intent_id TEXT);
      INSERT INTO execution_intents VALUES('intent');
      ALTER TABLE execution_intents ADD COLUMN state TEXT DEFAULT 'ACCEPTED';
      CREATE TABLE execution_fills(fill_id TEXT);
      CREATE TABLE execution_positions(instrument TEXT);
      INSERT INTO owner_metadata VALUES('scope','kis|acct|spot|paper');
      INSERT INTO owner_metadata VALUES('schema_version','4');
      INSERT INTO owner_metadata VALUES('account_version','1');
      INSERT INTO execution_fills VALUES('fill-1');
    """)
    db.execute("PRAGMA journal_mode=WAL")
    db.commit()
    db.execute("INSERT INTO execution_fills VALUES('fill-2')")
    db.commit()
    return db


def test_sqlite_wal_snapshot_keeps_latest_fill_and_never_overwrites(tmp_path):
    source = tmp_path / "ledger.sqlite"
    db = _db(source)
    source_hash_before = hashlib.sha256(source.read_bytes()).hexdigest()
    try:
        result = snapshot_sqlite(source, tmp_path / "snapshot.sqlite",
                                 expected_scope="kis|acct|spot|paper", expected_account_version=1)
        assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash_before
    finally:
        db.close()
    assert result["integrity"] == "ok"
    assert result["facts"]["facts"]["execution_fills"]["count"] == 2
    assert result["restore_test"] == "SECOND_NEW_COPY_READONLY_REOPEN_INTEGRITY_AND_FACTS"
    assert Path(result["restore_path"]).is_file()
    assert result["source_sha256_before"] == result["source_sha256_after"]
    from scripts.refactoring.deployment_checks import account_facts
    assert account_facts(source, "kis|acct|spot|paper")["facts"]["execution_fills"]["count"] == 2
    assert hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="unsafe"):
        snapshot_sqlite(source, tmp_path / "snapshot.sqlite",
                        expected_scope="kis|acct|spot|paper")


def test_sqlite_snapshot_rejects_stale_account_cut(tmp_path):
    db = _db(tmp_path / "ledger.sqlite")
    db.close()
    with pytest.raises(ValueError, match="version changed"):
        snapshot_sqlite(tmp_path / "ledger.sqlite", tmp_path / "snapshot.sqlite",
                        expected_scope="kis|acct|spot|paper", expected_account_version=0)
