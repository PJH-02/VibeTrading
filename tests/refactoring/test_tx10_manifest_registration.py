"""TX10 optional verified export-manifest binding for explicit file registration."""
from decimal import Decimal as D
import hashlib
import importlib
import json
import sys

import pytest

from test_strategy_runtime import T, book, deliver, ledger_at, matcher_for, runtime
from test_vn13_file_registration import (actions_for, configured, loaded, strategy_receipts,
                                         strategy_source)
from vibetrading.models import Side

RELATIVE = "market_data/event_type=trade/trading_date=2027-01-15/part-1.parquet"


@pytest.fixture
def strategy_files(tmp_path, monkeypatch):
    package = importlib.import_module("vibetrading.strategies")
    monkeypatch.setattr(package, "__path__", [*package.__path__, str(tmp_path)])
    created = []

    def write(name, source):
        (tmp_path / (name + ".py")).write_text(source, encoding="utf-8")
        monkeypatch.delitem(sys.modules, "vibetrading.strategies." + name, raising=False)
        created.append(name)
        return name

    yield write
    for name in created:
        sys.modules.pop("vibetrading.strategies." + name, None)


def export_row(root, relative, content, file_id, **changes):
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    row = {
        "file_id": file_id,
        "event_type": "trade",
        "trading_date": "2027-01-15",
        "provider": "test",
        "venue": "X",
        "rows": 1,
        "min_ts_ns": T - 2_000,
        "max_ts_ns": T - 1_000,
        "byte_size": len(content),
        "remote_key": "remote/" + file_id,
        "upload_status": "remote_verified",
        "created_ts_ns": T - 3_000,
        "relative_path": relative,
        "sha256": hashlib.sha256(content).hexdigest(),
        "source": "local",
    }
    row.update(changes)
    return row


def export_manifest(root, rows, *, lines=None):
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "manifest.jsonl"
    if lines is None:
        lines = [json.dumps(row, sort_keys=True) for row in rows]
    manifest.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return manifest


def descriptor(manifest, **changes):
    values = {
        "path": str(manifest),
        "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "available_at_ns": T,
        "max_manifest_bytes": 1_048_576,
        "max_files": 100,
        "max_total_bytes": 1_048_576,
    }
    values.update(changes)
    return values


def document_for(ledger, module, job_id, data_manifest):
    return configured(ledger, module, job_id, research_receipt={
        "research_id": "tx10-synthetic-export", "data_manifest": data_manifest})


def test_verified_manifest_binds_existing_export_and_places_paper_order(tmp_path, strategy_files):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        host = runtime(ledger, matcher, now)
        root = tmp_path / "export"
        first = b"tiny exporter object one"
        second = b"tiny exporter object two"
        rows = [
            export_row(root, RELATIVE, first, "file-1"),
            export_row(root, RELATIVE.replace("part-1", "part-2"), second, "file-2"),
        ]
        manifest = export_manifest(root, rows)
        desc = descriptor(manifest, max_files=2, max_total_bytes=len(first) + len(second))
        name = strategy_files("tx10_verified_file", strategy_source("tx10_verified", price="100"))
        host.register_configured(document_for(ledger, name, "tx10-verified-job", desc),
            action_builder=actions_for(ledger, "tx10_verified", "tx10-verified-job", Side.BUY))

        job = host._jobs["tx10_verified"]
        assert dict(job.registration.research_receipt["data_manifest"]) == desc
        assert job.registration.strategy_version == "strategy-1"
        assert job.registration.config_version == "config-1"
        assert D(job.registration.risk[ledger.scope.key].strategy_notional_limit) == D("100000")
        assert len(deliver(host, now, book())) == 1
        assert len(loaded(name).OBSERVED) == 1
        assert len(ledger.report()["orders"]) == 1
        assert host.report()["strategies"]["tx10_verified"]["callbacks"] == 1
        assert host.report()["strategies"]["tx10_verified"]["actions"] == 1
    finally:
        ledger.close()


def test_absent_optional_descriptor_keeps_existing_registration_behavior(tmp_path, strategy_files):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        host = runtime(ledger, matcher, now)
        name = strategy_files("tx10_absent_file", strategy_source("tx10_absent"))
        host.register_configured(configured(ledger, name, "tx10-absent-job"),
            action_builder=actions_for(ledger, "tx10_absent", "tx10-absent-job", Side.BUY))
        assert len(deliver(host, now, book())) == 1
        assert len(ledger.report()["orders"]) == 1
        assert "data_manifest" not in host._jobs["tx10_absent"].registration.research_receipt
    finally:
        ledger.close()


def test_checkpoint_identity_binds_manifest_descriptor(tmp_path, strategy_files):
    left_root, right_root = tmp_path / "left", tmp_path / "right"
    ledger_left, ledger_right = ledger_at(left_root), ledger_at(right_root)
    try:
        for ledger in (ledger_left, ledger_right):
            ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        root = tmp_path / "export"
        content = b"tiny exporter object"
        rows = [export_row(root, RELATIVE, content, "file-1")]
        manifest = export_manifest(root, rows)
        first = descriptor(manifest)
        second = descriptor(manifest, max_files=99)
        name = strategy_files("tx10_identity_file", strategy_source("tx10_identity"))
        left_now, right_now = [T], [T]
        host_left = runtime(ledger_left, matcher_for(ledger_left), left_now)
        host_right = runtime(ledger_right, matcher_for(ledger_right), right_now)
        host_left.register_configured(document_for(ledger_left, name, "tx10-identity-job", first),
            action_builder=actions_for(ledger_left, "tx10_identity", "tx10-identity-job", Side.BUY))
        host_right.register_configured(document_for(ledger_right, name, "tx10-identity-job", second),
            action_builder=actions_for(ledger_right, "tx10_identity", "tx10-identity-job", Side.BUY))

        sid = "tx10_identity"
        assert host_left._jobs[sid].fingerprint != host_right._jobs[sid].fingerprint
        left_identity, left_sha = host_left._runtime_identity()
        right_identity, right_sha = host_right._runtime_identity()
        assert left_identity["jobs"][sid]["fingerprint"] != right_identity["jobs"][sid]["fingerprint"]
        assert left_sha != right_sha

        host_right.begin_checkpoint_recovery()
        state = {"version": 1, "identity": left_identity, "identity_sha256": left_sha,
                 "jobs": [], "seen_actions": {}, "account_faults": {},
                 "invalid_inputs": {}, "deferred_input_gaps": {}}
        with pytest.raises(ValueError, match="identity mismatch"):
            host_right.restore_runtime_state(state, max_bytes=500_000)
    finally:
        ledger_left.close()
        ledger_right.close()


def test_changed_manifest_content_is_rejected_without_import_claim_or_order(tmp_path, strategy_files):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        host = runtime(ledger, matcher, now)
        marker = ("from pathlib import Path\n"
                  "Path(__file__).with_suffix('.imported').write_text('imported')\n")
        name = strategy_files("tx10_tampered_manifest",
            strategy_source("tx10_tampered_manifest", extra=marker))
        root = tmp_path / "export"
        rows = [export_row(root, RELATIVE, b"tiny exporter object", "file-1")]
        manifest = export_manifest(root, rows)
        desc = descriptor(manifest)
        manifest.write_text(manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        with pytest.raises(ValueError, match="checksum"):
            host.register_configured(document_for(ledger, name, "tx10-tamper-job", desc),
                action_builder=actions_for(ledger, "tx10_tampered_manifest",
                                           "tx10-tamper-job", Side.BUY))
        assert not (tmp_path / (name + ".imported")).exists()
        assert host.report()["strategies"] == {}
        assert strategy_receipts(ledger) == 0
        assert not ledger.report()["orders"]
    finally:
        ledger.close()


def test_changed_object_content_on_restart_is_rejected(tmp_path, strategy_files):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        host = runtime(ledger, matcher, now)
        marker = ("from pathlib import Path\n"
                  "Path(__file__).with_suffix('.imported').write_text('imported')\n")
        name = strategy_files("tx10_changed_object",
            strategy_source("tx10_changed_object", extra=marker))
        root = tmp_path / "export"
        rows = [export_row(root, RELATIVE, b"tiny exporter object", "file-1")]
        manifest = export_manifest(root, rows)
        desc = descriptor(manifest)
        (root / RELATIVE).write_bytes(b"changed exporter object")

        with pytest.raises(ValueError, match="byte size"):
            host.register_configured(document_for(ledger, name, "tx10-changed-job", desc),
                action_builder=actions_for(ledger, "tx10_changed_object",
                                           "tx10-changed-job", Side.BUY))
        assert not (tmp_path / (name + ".imported")).exists()
        assert host.report()["strategies"] == {}
        assert strategy_receipts(ledger) == 0
        assert not ledger.report()["orders"]
    finally:
        ledger.close()


@pytest.mark.parametrize("case", [
    "descriptor-not-mapping", "descriptor-missing-field", "descriptor-extra-field",
    "path-relative", "path-not-manifest", "path-missing",
    "descriptor-sha-uppercase", "descriptor-sha-short", "descriptor-sha-invalid",
    "available-future", "available-bool", "available-zero",
    "max-manifest-zero", "max-manifest-too-large", "max-manifest-bool",
    "max-files-zero", "max-files-too-large", "max-total-zero", "max-total-too-large",
    "manifest-empty", "manifest-malformed", "manifest-non-dict", "manifest-duplicate-keys",
    "manifest-symlink", "manifest-oversize",
    "row-missing-field", "row-file-id-empty", "row-file-id-duplicate",
    "row-relative-path-duplicate", "row-relative-absolute", "row-relative-traversal",
    "row-sha-invalid", "row-sha-uppercase", "row-byte-bool", "row-byte-negative",
    "row-min-negative", "row-max-negative", "row-min-greater-max", "row-max-future",
    "row-min-bool", "row-nan", "row-missing-object", "row-directory", "row-symlink",
    "row-size-mismatch", "row-hash-mismatch", "rows-too-many", "row-total-bytes",
])
def test_invalid_manifests_are_rejected_before_import_or_claims(tmp_path, strategy_files, case):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        host = runtime(ledger, matcher, now)
        strategy_id = "tx10_invalid_" + case.replace("-", "_")
        name = strategy_files(strategy_id, strategy_source(strategy_id))
        root = tmp_path / "export"
        content = b"tiny verified export object"
        row = export_row(root, RELATIVE, content, "file-1")
        rows, lines, changes = [row], None, {}
        target = root / RELATIVE

        if case == "manifest-empty":
            lines = []
        elif case == "manifest-malformed":
            lines = ["{not-json"]
        elif case == "manifest-non-dict":
            lines = ["[]"]
        elif case == "manifest-duplicate-keys":
            line = json.dumps(row, sort_keys=True)
            lines = [line.replace('"file_id": "file-1"',
                                  '"file_id": "file-1", "file_id": "file-2"', 1)]
        elif case == "row-missing-field":
            row.pop("max_ts_ns")
        elif case == "row-file-id-empty":
            row["file_id"] = ""
        elif case == "row-file-id-duplicate":
            rows.append(export_row(root, RELATIVE.replace("part-1", "part-2"),
                                   b"second exporter object", "file-1"))
        elif case == "row-relative-path-duplicate":
            rows.append(export_row(root, RELATIVE, content, "file-2"))
        elif case == "row-relative-absolute":
            row["relative_path"] = str(root / "escape.parquet")
        elif case == "row-relative-traversal":
            row["relative_path"] = "market_data/../../escape.parquet"
        elif case == "row-sha-invalid":
            row["sha256"] = "z" * 64
        elif case == "row-sha-uppercase":
            row["sha256"] = row["sha256"].upper()
        elif case == "row-byte-bool":
            row["byte_size"] = True
        elif case == "row-byte-negative":
            row["byte_size"] = -1
        elif case == "row-min-negative":
            row["min_ts_ns"] = -1
        elif case == "row-max-negative":
            row["max_ts_ns"] = -1
        elif case == "row-min-greater-max":
            row["min_ts_ns"], row["max_ts_ns"] = T, T - 1_000
        elif case == "row-max-future":
            row["max_ts_ns"] = T + 1
        elif case == "row-min-bool":
            row["min_ts_ns"] = True
        elif case == "row-nan":
            row["byte_size"] = float("nan")
        elif case == "row-missing-object":
            target.unlink()
        elif case == "row-directory":
            target.unlink()
            target.mkdir()
        elif case == "row-symlink":
            outside = tmp_path / "outside.bin"
            outside.write_bytes(content)
            target.unlink()
            target.symlink_to(outside)
        elif case == "row-size-mismatch":
            row["byte_size"] = len(content) + 1
        elif case == "row-hash-mismatch":
            row["sha256"] = "0" * 64
        elif case == "rows-too-many":
            rows.append(export_row(root, RELATIVE.replace("part-1", "part-2"),
                                   b"second exporter object", "file-2"))
            changes["max_files"] = 1
        elif case == "row-total-bytes":
            changes["max_total_bytes"] = len(content) - 1

        manifest = export_manifest(root, rows, lines=lines)
        desc = descriptor(manifest, **changes)

        if case == "descriptor-not-mapping":
            desc = ["not-a-mapping"]
        elif case == "descriptor-missing-field":
            desc.pop("max_files")
        elif case == "descriptor-extra-field":
            desc["unexpected"] = "value"
        elif case == "path-relative":
            desc["path"] = "manifest.jsonl"
        elif case == "path-not-manifest":
            other = root / "catalog.jsonl"
            other.write_bytes(manifest.read_bytes())
            desc["path"] = str(other)
        elif case == "path-missing":
            desc["path"] = str(root / "missing" / "manifest.jsonl")
        elif case == "descriptor-sha-uppercase":
            desc["sha256"] = desc["sha256"].upper()
        elif case == "descriptor-sha-short":
            desc["sha256"] = "a" * 63
        elif case == "descriptor-sha-invalid":
            desc["sha256"] = "g" * 64
        elif case == "available-future":
            desc["available_at_ns"] = T + 1
        elif case == "available-bool":
            desc["available_at_ns"] = True
        elif case == "available-zero":
            desc["available_at_ns"] = 0
        elif case == "max-manifest-zero":
            desc["max_manifest_bytes"] = 0
        elif case == "max-manifest-too-large":
            desc["max_manifest_bytes"] = 1_048_577
        elif case == "max-manifest-bool":
            desc["max_manifest_bytes"] = True
        elif case == "max-files-zero":
            desc["max_files"] = 0
        elif case == "max-files-too-large":
            desc["max_files"] = 10_001
        elif case == "max-total-zero":
            desc["max_total_bytes"] = 0
        elif case == "max-total-too-large":
            desc["max_total_bytes"] = 1_073_741_825
        elif case == "manifest-symlink":
            alias = tmp_path / "alias"
            alias.mkdir()
            link = alias / "manifest.jsonl"
            link.symlink_to(manifest)
            desc["path"] = str(link)
        elif case == "manifest-oversize":
            desc["max_manifest_bytes"] = 1

        with pytest.raises(ValueError):
            host.register_configured(document_for(ledger, name, "tx10-invalid-job", desc),
                action_builder=actions_for(ledger, strategy_id, "tx10-invalid-job", Side.BUY))
        assert host.report()["strategies"] == {}
        assert strategy_receipts(ledger) == 0
        assert not ledger.report()["orders"]
    finally:
        ledger.close()
