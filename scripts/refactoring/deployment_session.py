"""Fail-closed local release installation and evidence session.

This is an offline planning tool. Its only writable operation is an explicitly
marked private LOCAL_FAKE sandbox stage; it cannot start a service or authorize
production activation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

# Support `python /absolute/path/scripts/refactoring/deployment_session.py`
# from a cleared environment and any working directory.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_PROJECT_ROOT), str(_PROJECT_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from scripts.refactoring import release_candidate
from scripts.refactoring.deployment_checks import validate_payloads

SCHEMA = 1
MAX_JSON = 8 * 1024 * 1024
MAX_FILES = 12000
LOCAL_MARKER = "LOCAL_FAKE: DEP-01 PRIVATE SANDBOX ONLY"
HOST_FIELDS = {"evidence_class", "observed_ns", "source_sha256", "manifest_sha256", "artifacts"}
HOST_ARTIFACTS = {"mounts", "owner", "sqlite", "latest_truth", "disk", "spool",
                  "health", "paper_data", "kill_switch"}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _no_symlink_path(path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("path must be absolute")
    absolute = path.absolute()
    if ".." in absolute.parts or "\\" in str(absolute) or "\x00" in str(absolute):
        raise ValueError("path is noncanonical")
    normalized = Path(os.path.normpath(str(absolute)))
    if normalized != absolute:
        raise ValueError("path is noncanonical")
    if any(part.is_symlink() for part in (absolute, *absolute.parents)):
        raise ValueError("path ancestor symlink rejected")
    return absolute


def _json(path: Path) -> dict:
    path = _no_symlink_path(path)
    if path.is_symlink() or path.stat().st_size > MAX_JSON:
        raise ValueError("bounded JSON input rejected")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("JSON input invalid") from None
    if not isinstance(value, dict):
        raise ValueError("JSON document must be an object")
    return value


def _safe_root(root: Path) -> Path:
    root = Path(root)
    if not root.is_absolute() or root.is_symlink():
        raise ValueError("root must be an absolute non-symlink directory")
    absolute = root.absolute()
    if any(part.is_symlink() for part in (absolute, *absolute.parents)):
        raise ValueError("root ancestor symlink rejected")
    resolved = root.resolve(strict=True)
    if resolved == Path(resolved.anchor) or resolved != root.absolute():
        raise ValueError("unsafe root path")
    return resolved


def _validated_entries(release: Path, entries: dict) -> None:
    if not isinstance(entries, dict) or not entries or len(entries) > MAX_FILES:
        raise ValueError("frozen file inventory invalid")
    for rel, entry in entries.items():
        if not isinstance(rel, str):
            raise ValueError("unsafe frozen release path")
        pure = PurePosixPath(rel)
        if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts) or "\\" in rel:
            raise ValueError("unsafe frozen release path")
        source = release.joinpath(*pure.parts)
        if source.is_symlink() or not source.is_file() or _sha(source) != entry.get("sha256"):
            raise ValueError("frozen release source changed")


def _seal(session: dict) -> str:
    body = {k: v for k, v in session.items() if k != "session_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _role_rows(manifest: dict) -> list[dict]:
    result = []
    for name, item in sorted((manifest.get("roles") or {}).items()):
        if not isinstance(item, dict):
            continue
        image_class = item.get("image_class")
        image_facts = (manifest.get("images") or {}).get(image_class, {})
        result.append({"role": name, "image_class": image_class,
                       "image_id": image_facts.get("id"), "image_source_sha256": image_facts.get("source_sha256"),
                       "image_config_sha256": image_facts.get("config_sha256"),
                       "image_command": image_facts.get("command"), "compose": item.get("compose"),
                       "service": item.get("service"), "command": item.get("command"),
                       "profiles": item.get("profiles", []),
                       "environment_names": item.get("environment_names", []),
                       "env_file_names": item.get("env_file_names", []),
                       "secret_names": item.get("secret_names", []),
                       "mounts": item.get("mounts", []), "healthcheck": item.get("healthcheck"),
                       "restart": item.get("restart"), "stop_grace_period": item.get("stop_grace_period"),
                       "submit": item.get("submit"), "classification": item.get("inventory_state")})
    historic = (manifest.get("server_inventory") or {}).get("roles", [])
    for item in historic:
        if isinstance(item, dict):
            result.append({"role": item.get("container"), "image": item.get("image"),
                           "config": None, "mounts": None,
                           "classification": "HISTORICAL_UNVERIFIED_CURRENT_UNKNOWN",
                           "observed_utc": item.get("observed_utc"), "mapping": item.get("mapping")})
    return result


def _gate(gate_id: str, group: str, status: str, reason: str, **details) -> dict:
    return {"gate_id": gate_id, "group": group, "status": status, "reason": reason, **details}


def validate_import_origins(release_root: Path) -> dict:
    """Resolve canonical package specs from release root and unrelated /tmp cwd."""
    root = _safe_root(Path(release_root))
    src = root / "src"
    script = ("import importlib.util,json,pathlib,sys;"
              "root=pathlib.Path(sys.argv[1]).resolve();"
              "names=('core','feeds','market','storage','strategies','execution','portfolio','runtime','operations','timefolio');"
              "out={};"
              "exec('for n in names:\\n s=importlib.util.find_spec(n)\\n p=(s.origin if s and s.origin else next(iter(s.submodule_search_locations),None) if s else None)\\n t=pathlib.Path(p).resolve() if p else None\\n if t is None or not t.is_relative_to(root): raise SystemExit(31)\\n out[n]=str(t)');"
              "print(json.dumps(out,sort_keys=True))")
    observations = {}
    for label, cwd in (("release-root", root), ("external-cwd", Path("/tmp"))):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
               "PYTHONPATH": str(src), "PYTHONNOUSERSITE": "1"}
        try:
            proc = subprocess.run([sys.executable, "-c", script, str(src)], cwd=cwd,
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError("bounded import-origin probe failed") from None
        if proc.returncode != 0:
            raise ValueError("canonical source import shadow detected")
        try:
            result = json.loads(proc.stdout.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise ValueError("canonical source import report malformed") from None
        if set(result) != {"core", "feeds", "market", "storage", "strategies", "execution",
                           "portfolio", "runtime", "operations", "timefolio"}:
            raise ValueError("canonical source import inventory incomplete")
        observations[label] = result
    return observations


def create_session(manifest_path: Path, expected_manifest_sha256: str, release_root: Path,
                   evidence_root: Path, *, wheel_path: Path | None = None,
                   expected_wheel_sha256: str | None = None, host_facts: dict | None = None,
                   now_ns: int | None = None) -> dict:
    """Verify frozen release bytes and create a read-only, externally blocked session."""
    wall_now = time.time_ns()
    if now_ns is None and isinstance(host_facts, dict) and type(host_facts.get("observed_ns")) is int:
        timestamp = host_facts["observed_ns"]
        if timestamp > wall_now or wall_now - timestamp > 300_000_000_000:
            raise ValueError("host evidence timestamp is stale or future-dated")
    else:
        timestamp = int(wall_now if now_ns is None else now_ns)
    release_root = _safe_root(release_root)
    evidence_root = _safe_root(Path(evidence_root))
    manifest_path = _no_symlink_path(Path(manifest_path))
    manifest = _json(manifest_path)
    if manifest.get("manifest_sha256") != expected_manifest_sha256:
        raise ValueError("manifest trust anchor mismatch")
    # Reuse the accepted REL verifier including source tree and wheel RECORD checks.
    wheel_arg = None
    if wheel_path is not None:
        wheel_path = _no_symlink_path(Path(wheel_path))
        wheel_arg = {"path": str(wheel_path),
                     "expected_sha256": expected_wheel_sha256 or manifest.get("wheel", {}).get("sha256", "")}
    release_candidate.verify(release_root, manifest,
                             expected_head=manifest.get("source_head"), wheel=wheel_arg,
                             expected_manifest_sha256=expected_manifest_sha256)
    if not wheel_arg:
        raise ValueError("actual verified wheel archive is required")
    wheel_verified = release_candidate.verify_wheel(
        release_root, Path(wheel_arg["path"]), wheel_arg["expected_sha256"])
    wheel_path = _no_symlink_path(Path(wheel_path))
    source_origins = validate_import_origins(release_root)
    lineage = {"manifest": {"path": str(manifest_path), "sha256": _sha(manifest_path)},
               "wheel": {"path": str(wheel_path), "sha256": wheel_verified["sha256"]},
               "source_head": manifest.get("source_head"), "source_sha256": manifest.get("source_sha256"),
               "config_sha256": manifest.get("config_sha256"), "input_sha256": manifest.get("input_sha256")}
    external = host_facts is None
    if host_facts is not None:
        if not isinstance(host_facts, dict) or set(host_facts) != HOST_FIELDS:
            raise ValueError("host facts do not match the bounded schema")
        if host_facts.get("evidence_class") != "LOCAL_FAKE":
            raise ValueError("host facts must be explicitly classified LOCAL_FAKE")
        if (host_facts.get("observed_ns") != timestamp or
                host_facts.get("source_sha256") != manifest.get("source_sha256") or
                host_facts.get("manifest_sha256") != manifest.get("manifest_sha256")):
            raise ValueError("host evidence timestamp/source/manifest mismatch")
        if not isinstance(host_facts.get("artifacts"), dict) or set(host_facts["artifacts"]) != HOST_ARTIFACTS:
            raise ValueError("host evidence artifact inventory incomplete")
        expected_session = str(uuid.uuid5(uuid.NAMESPACE_URL,
            manifest["manifest_sha256"] + ":" + manifest["source_sha256"] + ":" + str(timestamp)))
        normalized_evidence = {}
        for group, proof in host_facts["artifacts"].items():
            if not isinstance(proof, dict) or set(proof) != {"path", "sha256"}:
                raise ValueError("host evidence reference invalid: " + group)
            path = Path(proof["path"])
            if path.is_symlink() or not path.is_absolute() or not path.is_file() or _sha(path) != proof["sha256"]:
                raise ValueError("host evidence artifact missing or changed: " + group)
            envelope = _json(path)
            if (set(envelope) != {"evidence_class", "observed_ns", "source_sha256", "manifest_sha256",
                                  "session_id", "group", "payload"} or
                    envelope.get("evidence_class") != "LOCAL_FAKE" or
                    envelope.get("observed_ns") != timestamp or
                    envelope.get("source_sha256") != manifest["source_sha256"] or
                    envelope.get("manifest_sha256") != manifest["manifest_sha256"] or
                    envelope.get("session_id") != expected_session or envelope.get("group") != group):
                raise ValueError("host evidence artifact lineage mismatch: " + group)
            normalized_evidence[group] = {"path": str(path), "sha256": proof["sha256"],
                "payload": envelope["payload"], "observed_ns": envelope["observed_ns"]}
        operational = validate_payloads(normalized_evidence, now_ns=timestamp,
            session_id=expected_session, source_sha256=manifest["source_sha256"],
            manifest_sha256=manifest["manifest_sha256"],
            expected_schema=manifest["execution_ledger_schema_version"])
    else:
        operational = None
    gates = [
        _gate("PRE-RELEASE-IDENTITY", "release", "PASS", "REL verifier matched source, image and wheel evidence"),
        _gate("PRE-IMAGE-MISMATCH", "release",
              "PASS" if set((manifest.get("images") or {})) == set(release_candidate.IMAGE_CLASSES) else "FAIL",
              "all four frozen image classes verified" if set((manifest.get("images") or {})) == set(release_candidate.IMAGE_CLASSES) else "four-class image identity proof absent"),
        _gate("PRE-STALE-CMD", "release", "PASS", "role commands verified by frozen release"),
        _gate("PRE-ROOT-STRATEGIES-SHADOW", "release", "PASS",
              "clean subprocess import origins verified from release root and external cwd"),
        _gate("PRE-TIMEFOLIO", "role",
              "PASS_FOR_BLOCKED_SUBMIT" if any(r.get("submit") is False and r.get("image_class") == "browser" for r in (manifest.get("roles") or {}).values()) else "FAIL",
              "submit false and browser class frozen; account arming remains external"),
        _gate("PRE-EMPTY-MOUNT", "storage", "PASS" if operational else "NOT_RUN",
              "actual mount identity checked" if operational else "mount proof absent"),
        _gate("PRE-NATS-ONLY", "rollback", "PASS", "primary release is NATS-free; NATS services are rollback-only"),
        _gate("PRE-CURRENT-HOST", "host", "EXTERNAL_ONLY",
             "production host facts are unverified" if external else "LOCAL_FAKE evidence cannot prove production"),
        _gate("PRE-ACCOUNT-TRUTH", "account", "PASS" if operational else "EXTERNAL_ONLY",
              "local cut verified; production remains external" if operational else "current account truth unavailable"),
        _gate("PRE-DISK-RESERVE", "storage", "PASS" if operational else "NOT_RUN",
              "statvfs reserve checked" if operational else "disk proof absent"),
        _gate("PRE-SPOOL-CLOUD", "storage", "PASS" if operational else "NOT_RUN",
              "local spool prefix checked; cloud remains external" if operational else "spool proof absent"),
        _gate("PRE-HEALTH-TELEGRAM", "health", "PASS" if operational else "NOT_RUN",
              "source-bound local cold health" if operational else "health proof absent"),
        _gate("PRE-PAPER-DATA", "runtime", "PASS" if operational else "NOT_RUN",
              "source-bound local PAPER/data" if operational else "PAPER/data proof absent"),
    ]
    session_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                     manifest["manifest_sha256"] + ":" + manifest["source_sha256"] + ":" + str(timestamp)))
    evidence_refs = (host_facts.get("artifacts") if host_facts else {})
    for gate in gates:
        group = {"PRE-EMPTY-MOUNT": "mounts", "PRE-DISK-RESERVE": "disk",
                 "PRE-ACCOUNT-TRUTH": "latest_truth", "PRE-SPOOL-CLOUD": "spool",
                 "PRE-HEALTH-TELEGRAM": "health", "PRE-PAPER-DATA": "paper_data",
                 "PRE-CURRENT-HOST": "mounts", "PRE-IMAGE-MISMATCH": "release",
                 "PRE-STALE-CMD": "release", "PRE-ROOT-STRATEGIES-SHADOW": "release",
                 "PRE-TIMEFOLIO": "release", "PRE-NATS-ONLY": "release"}.get(gate["gate_id"])
        ref = evidence_refs.get(group) if group else None
        gate.update({"input_timestamp": timestamp, "frozen_sha": manifest["source_sha256"],
                     "session_id": session_id,
                     "evidence_path": ref.get("path") if ref else str(manifest_path),
                     "evidence_sha256": ref.get("sha256") if ref else manifest["manifest_sha256"]})
    result = {"schema_version": SCHEMA, "session_id": session_id,
            "created_ns": timestamp, "input_timestamp": timestamp,
            "frozen_sha": manifest.get("source_sha256"),
            "manifest_sha256": manifest["manifest_sha256"], "release_root": str(release_root),
            "evidence_root": str(evidence_root),
            "lineage": lineage, "anchors": {"manifest_path": str(manifest_path.absolute()),
                "wheel_path": str(Path(wheel_path).absolute()), "wheel_sha256": wheel_verified["sha256"],
                "expected_manifest_sha256": expected_manifest_sha256,
                "evidence_artifacts": ({g: {"path": x["path"], "sha256": x["sha256"]}
                                        for g, x in host_facts["artifacts"].items()} if host_facts else None)},
            "operational_evidence": operational,
            "source_import_origins": source_origins,
            "roles": _role_rows(manifest), "gates": gates,
            "deployment_state": "DEPLOY_BLOCKED", "p0_latched": False,
            "install": {"state": "NOT_INSTALLED", "stage": None},
            "evidence_class": "LOCAL_FAKE" if host_facts else "UNVERIFIED",
            "activation": "EXTERNAL_ONLY_CUT01_REQUIRED", "database_restore": "PROHIBITED"}
    result["session_sha256"] = _seal(result)
    return result


def _load_session(value: dict) -> dict:
    if value.get("schema_version") != SCHEMA or not value.get("session_id") or not value.get("frozen_sha"):
        raise ValueError("session identity invalid")
    if value.get("session_sha256") != _seal(value):
        raise ValueError("session phase/input seal mismatch")
    if len(value.get("gates", [])) > 256 or len(value.get("roles", [])) > 256:
        raise ValueError("session entry count exceeds bound")
    return value


def _revalidate(session: dict) -> dict:
    """Re-open every authoritative input; never use a stored PASS as current proof."""
    anchors = session.get("anchors")
    if not isinstance(anchors, dict) or set(anchors) != {"manifest_path", "wheel_path", "wheel_sha256",
                                                        "expected_manifest_sha256", "evidence_artifacts"}:
        raise ValueError("session lacks authoritative release anchors")
    manifest_path = _no_symlink_path(Path(anchors["manifest_path"]))
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("frozen manifest anchor missing")
    manifest = _json(manifest_path)
    if _sha(manifest_path) != session["lineage"]["manifest"]["sha256"]:
        raise ValueError("frozen manifest changed since planning")
    root = _safe_root(Path(session["release_root"]))
    wheel = _no_symlink_path(Path(anchors["wheel_path"]))
    if wheel.is_symlink() or not wheel.is_file() or _sha(wheel) != anchors["wheel_sha256"]:
        raise ValueError("verified wheel changed since planning")
    release_candidate.verify(root, manifest, expected_head=session["lineage"]["source_head"],
        wheel={"path": str(wheel), "expected_sha256": anchors["wheel_sha256"]},
        expected_manifest_sha256=anchors["expected_manifest_sha256"])
    source_origins = validate_import_origins(root)
    if source_origins != session.get("source_import_origins"):
        raise ValueError("canonical source import origins changed since planning")
    if (set(manifest.get("images", {})) != set(release_candidate.IMAGE_CLASSES) or
            manifest.get("deployment_state") != "CANDIDATE_PENDING_INDEPENDENT_IMAGE_AND_COMPOSE_VERIFICATION"):
        raise ValueError("frozen candidate lacks all four verified image classes")
    timefolio = [item for item in manifest.get("roles", {}).values()
                 if item.get("image_class") == "browser" and item.get("submit") is False]
    if not any("--no-submit" in item.get("command", []) and "--submit" not in item.get("command", [])
               for item in timefolio):
        raise ValueError("frozen Timefolio role is not explicitly submit-blocked")
    if (manifest.get("source_sha256") != session["frozen_sha"] or
            manifest.get("manifest_sha256") != session["manifest_sha256"]):
        raise ValueError("frozen release identity differs from session")
    if session.get("roles") != _role_rows(manifest):
        raise ValueError("role/image/config inventory differs from frozen manifest")
    refs = anchors.get("evidence_artifacts")
    if not isinstance(refs, dict) or set(refs) != HOST_ARTIFACTS:
        raise ValueError("required local operational evidence is absent")
    now = time.time_ns()
    timestamp = session["input_timestamp"]
    evidence = {}
    for group, item in refs.items():
        path = Path(item["path"])
        if path.is_symlink() or not path.is_file() or _sha(path) != item["sha256"]:
            raise ValueError("operational evidence changed: " + group)
        body = _json(path)
        age = now - body.get("observed_ns", 0)
        if (body.get("session_id") != session["session_id"] or body.get("group") != group or
            body.get("source_sha256") != session["frozen_sha"] or
            body.get("manifest_sha256") != session["manifest_sha256"] or
            body.get("evidence_class") != "LOCAL_FAKE" or
            body.get("observed_ns") != timestamp or age < 0 or age > 300_000_000_000):
            raise ValueError("operational evidence lineage/freshness mismatch: " + group)
        evidence[group] = {"path": item["path"], "sha256": item["sha256"], "payload": body.get("payload"),
                           "observed_ns": body["observed_ns"]}
    operational = validate_payloads(evidence, now_ns=now, session_id=session["session_id"],
        source_sha256=session["frozen_sha"], manifest_sha256=session["manifest_sha256"],
        expected_schema=manifest["execution_ledger_schema_version"])
    statuses = {"PRE-RELEASE-IDENTITY": "PASS", "PRE-IMAGE-MISMATCH": "PASS",
        "PRE-STALE-CMD": "PASS", "PRE-ROOT-STRATEGIES-SHADOW": "PASS",
        "PRE-TIMEFOLIO": "PASS_FOR_BLOCKED_SUBMIT", "PRE-EMPTY-MOUNT": "PASS",
        "PRE-NATS-ONLY": "PASS", "PRE-CURRENT-HOST": "EXTERNAL_ONLY",
        "PRE-ACCOUNT-TRUTH": "PASS", "PRE-DISK-RESERVE": "PASS",
        "PRE-SPOOL-CLOUD": "PASS", "PRE-HEALTH-TELEGRAM": "PASS", "PRE-PAPER-DATA": "PASS"}
    stored = {item.get("gate_id"): item for item in session.get("gates", [])}
    if set(stored) != set(statuses):
        raise ValueError("gate inventory differs from current validator set")
    for gate_id, status in statuses.items():
        item = stored[gate_id]
        if (item.get("status") != status or item.get("session_id") != session["session_id"] or
                item.get("frozen_sha") != session["frozen_sha"] or
                item.get("input_timestamp") != session["input_timestamp"]):
            raise ValueError("stored gate report was edited or mixed: " + gate_id)
        group = {"PRE-EMPTY-MOUNT": "mounts", "PRE-DISK-RESERVE": "disk",
                 "PRE-ACCOUNT-TRUTH": "latest_truth", "PRE-SPOOL-CLOUD": "spool",
                 "PRE-HEALTH-TELEGRAM": "health", "PRE-PAPER-DATA": "paper_data",
                 "PRE-CURRENT-HOST": "mounts"}.get(gate_id)
        ref = refs.get(group) if group else None
        expected_path = ref["path"] if ref else str(manifest_path)
        expected_hash = ref["sha256"] if ref else manifest["manifest_sha256"]
        if item.get("evidence_path") != expected_path or item.get("evidence_sha256") != expected_hash:
            raise ValueError("stored gate evidence pointer was edited: " + gate_id)
    return operational


def _checked_revalidate(session: dict) -> dict:
    try:
        return _revalidate(session)
    except (OSError, ValueError, RuntimeError, sqlite3.Error, KeyError, TypeError):
        # Latch the failure durably before propagating a redacted exception.
        marker = _p0_path(session)
        if not marker.exists() and not session.get("p0_latched"):
            latch_p0(session, "operational preflight failed")
        raise ValueError("operational preflight failed; P0 read-only latch recorded") from None


def latch_p0(session: dict, reason: str) -> dict:
    """Create a sealed read-only session version after a P0 fault."""
    session = _load_session(dict(session))
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 256:
        raise ValueError("bounded P0 reason required")
    state = _p0_path(session)
    if not state.parent.is_dir() or state.parent.is_symlink():
        raise ValueError("P0 latch root is absent or unsafe")
    fd = os.open(state, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(hashlib.sha256(reason.encode("utf-8")).hexdigest() + "\n")
        stream.flush(); os.fsync(stream.fileno())
    result = {**session, "p0_latched": True,
              "p0_reason": hashlib.sha256(reason.encode("utf-8")).hexdigest()}
    result["session_sha256"] = _seal(result)
    return result


def _p0_path(session: dict) -> Path:
    # Candidate identity prevents reset by changing only a session timestamp.
    identity = session.get("manifest_sha256")
    if not isinstance(identity, str) or len(identity) != 64:
        raise ValueError("P0 latch requires frozen manifest identity")
    return Path(session["evidence_root"]) / ("P0-" + identity)


def _install_path(session: dict) -> Path:
    return Path(session["evidence_root"]) / ("INSTALL-" + session["manifest_sha256"])


def snapshot_sqlite(source: Path, destination: Path, *, expected_scope: str,
                    expected_schema: int = 4, expected_account_version: int | None = None) -> dict:
    """WAL-safe SQLite snapshot into a fresh destination; never overwrites data."""
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or not source.is_file() or destination.exists() or destination.is_symlink():
        raise ValueError("snapshot source/destination is unsafe")
    if any(part.is_symlink() for part in (source.absolute(), *source.absolute().parents)):
        raise ValueError("snapshot source ancestor symlink rejected")
    source_sha_before = _sha(source)
    if not destination.parent.is_dir() or destination.parent.resolve() != destination.parent.absolute():
        raise ValueError("snapshot destination parent must already exist and be private")
    uri = source.resolve().as_uri() + "?mode=ro"
    src = sqlite3.connect(uri, uri=True, timeout=2)
    try:
        src.execute("PRAGMA query_only=ON")
        src.execute("BEGIN")
        check = src.execute("PRAGMA integrity_check").fetchone()[0]
        meta = dict(src.execute("SELECT key,value FROM owner_metadata WHERE key IN ('scope','schema_version','account_version')"))
        if check != "ok" or meta.get("scope") != expected_scope:
            raise ValueError("account database identity/integrity mismatch")
        if int(meta.get("schema_version", -1)) != expected_schema:
            raise ValueError("account database schema mismatch")
        version = int(meta.get("account_version", -1))
        if expected_account_version is not None and version != expected_account_version:
            raise ValueError("account database version changed")
        from scripts.refactoring.deployment_checks import account_facts, account_facts_connection
        facts = account_facts_connection(src, expected_scope)
        # Reserve the destination atomically; no existing file can be truncated.
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
            dst.commit()
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        finally:
            dst.close()
    finally:
        if src.in_transaction:
            src.rollback()
        src.close()
    check_db = sqlite3.connect(f"file:{destination}?mode=ro", uri=True)
    try:
        check_db.execute("PRAGMA query_only=ON")
        check_db.execute("BEGIN")
        check_after = check_db.execute("PRAGMA integrity_check").fetchone()[0]
        meta_after = dict(check_db.execute("SELECT key,value FROM owner_metadata WHERE key IN ('scope','schema_version','account_version')"))
        facts_after = account_facts_connection(check_db, expected_scope)
    finally:
        if check_db.in_transaction:
            check_db.rollback()
        check_db.close()
    if check_after != "ok" or meta_after != meta or facts_after != facts:
        destination.unlink(missing_ok=True)
        raise ValueError("snapshot verification mismatch")
    restored = destination.with_name(destination.name + ".restore-check")
    restore_fd = os.open(restored, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    os.close(restore_fd)
    original = sqlite3.connect(destination.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    restored_db = sqlite3.connect(restored)
    try:
        original.execute("PRAGMA query_only=ON")
        original.backup(restored_db)
        restored_db.commit()
    finally:
        restored_db.close(); original.close()
    restored_facts = account_facts(restored, expected_scope)
    if restored_facts != facts:
        destination.unlink(missing_ok=True); restored.unlink(missing_ok=True)
        raise ValueError("restore-to-new-copy facts mismatch")
    source_sha_after = _sha(source)
    if source_sha_after != source_sha_before:
        destination.unlink(missing_ok=True); restored.unlink(missing_ok=True)
        raise ValueError("snapshot operation changed source bytes")
    return {"sha256": _sha(destination), "integrity": check_after, "scope": expected_scope,
            "schema_version": expected_schema, "account_version": version, "facts": facts_after,
            "restore_path": str(restored), "restore_sha256": _sha(restored),
            "restore_test": "SECOND_NEW_COPY_READONLY_REOPEN_INTEGRITY_AND_FACTS",
            "source_sha256_before": source_sha_before, "source_sha256_after": source_sha_after,
            "source_unchanged": True}


def run_mode(session: dict, mode: str, *, sandbox_root: Path | None = None,
             local_fake_marker: str | None = None) -> dict:
    """Run one bounded mode. Only explicit private LOCAL_FAKE install can write."""
    session = _load_session(dict(session))
    if mode not in {"plan", "preflight", "install-blocked", "verify", "activate", "rollback"}:
        raise ValueError("unknown deployment mode")
    if mode == "plan":
        result = {**session, "p0_latched": bool(session.get("p0_latched") or _p0_path(session).exists())}
        result["session_sha256"] = _seal(result)
        return result
    if mode == "preflight":
        operational = _checked_revalidate(session)
        latched = bool(session.get("p0_latched") or _p0_path(session).exists())
        result = {**session, "operational_evidence": operational,
                  "preflight": "P0_READ_ONLY" if latched else "LOCAL_GATES_PASS_EXTERNAL_BLOCKED",
                  "mutation_count": 0}
        result["session_sha256"] = _seal(result)
        return result
    if mode == "activate":
        result = {**session, "activation": "BLOCKED", "reason": "CUT-01 approval and external production evidence required",
                  "mutation_count": 0}
        result["session_sha256"] = _seal(result)
        return result
    if mode == "rollback":
        if session.get("p0_latched") or _p0_path(session).exists():
            result = {**session, "rollback": "P0_BLOCKED", "database_restore": "PROHIBITED",
                      "reason": "candidate P0 latch permits inspection only", "mutation_count": 0}
            result["session_sha256"] = _seal(result)
            return result
        operational = _checked_revalidate(session)
        result = {**session, "rollback": "PLAN_ONLY", "database_restore": "PROHIBITED",
                  "operational_evidence": operational,
                  "reason": "binary-only plan; production authority remains external", "mutation_count": 0}
        result["session_sha256"] = _seal(result)
        return result
    if mode == "verify":
        operational = _checked_revalidate(session)
        stage = session.get("install", {}).get("stage")
        if not stage:
            result = {**session, "verification": "FAIL", "reason": "no staged install", "mutation_count": 0}
            result["session_sha256"] = _seal(result)
            return result
        root = Path(stage)
        ok = False
        try:
            receipt = _json(root / "release_verification.json")
            manifest = _json(Path(session["lineage"]["manifest"]["path"]))
            receipt_ops = receipt.get("operational_evidence")
            current_ops = operational
            expected_groups = {"mounts", "disk", "owner", "sqlite", "latest_truth", "spool",
                               "kill_switch", "health", "paper_data"}
            ops_valid = (isinstance(receipt_ops, dict) and
                set(receipt_ops) == {"results", "verified_roots"} and
                isinstance(receipt_ops.get("results"), dict) and
                set(receipt_ops["results"]) == expected_groups and
                all(isinstance(row, dict) and row.get("status") == "PASS"
                    for row in receipt_ops["results"].values()) and
                receipt_ops.get("verified_roots") == current_ops.get("verified_roots") and
                all(receipt_ops["results"][name].get("status") == current_ops["results"][name].get("status")
                    for name in expected_groups))
            ok = (root.is_dir() and not root.is_symlink() and
                  receipt.get("session_id") == session["session_id"] and
                  receipt.get("frozen_sha") == session["frozen_sha"] and
                  receipt.get("manifest_sha256") == session["manifest_sha256"] and
                  receipt.get("status") == "INSTALLED_BLOCKED" and
                  receipt.get("roles_started") is False and
                  receipt.get("wheel_sha256") == session["anchors"]["wheel_sha256"] and
                  receipt.get("gates") == session["gates"] and
                  receipt.get("roles") == session["roles"] and
                  receipt.get("lineage") == session["lineage"] and
                  receipt.get("source_import_origins") == session["source_import_origins"] and
                  ops_valid)
            for rel, entry in manifest.get("files", {}).items():
                staged = root.joinpath(*PurePosixPath(rel).parts)
                if staged.is_symlink() or not staged.is_file() or _sha(staged) != entry["sha256"]:
                    ok = False
                    break
            wheel_staged = root / "artifacts" / "vibetrading.whl"
            if not wheel_staged.is_file() or wheel_staged.is_symlink() or _sha(wheel_staged) != session["anchors"]["wheel_sha256"]:
                ok = False
        except (OSError, ValueError, KeyError, TypeError):
            ok = False
        result = {**session, "operational_evidence": operational,
                  "verification": "PASS" if ok else "FAIL", "mutation_count": 0}
        if not ok and not _p0_path(session).exists():
            result = latch_p0(session, "staged release verification failed")
            result.update({"operational_evidence": operational, "verification": "FAIL", "mutation_count": 0})
        result["session_sha256"] = _seal(result)
        return result
    if session.get("p0_latched") or _p0_path(session).exists():
        raise ValueError("P0 fault latch prohibits installation")
    operational = _checked_revalidate(session)
    if any(item["status"] != "PASS" for item in operational["results"].values()):
        raise ValueError("required operational preflight gates have not passed")
    if sandbox_root is None or local_fake_marker != LOCAL_MARKER:
        raise ValueError("install requires explicit private sandbox and exact LOCAL_FAKE marker")
    sandbox_root = Path(sandbox_root)
    if sandbox_root.is_symlink() or not sandbox_root.is_absolute() or not sandbox_root.is_dir():
        raise ValueError("sandbox root must be an existing absolute non-symlink mount")
    if any(part.is_symlink() for part in (sandbox_root.absolute(), *sandbox_root.absolute().parents)):
        raise ValueError("sandbox root ancestor symlink rejected")
    if sandbox_root.resolve() == Path(sandbox_root.anchor):
        raise ValueError("sandbox root fallback rejected")
    if str(sandbox_root.resolve()) not in operational["verified_roots"]:
        raise ValueError("sandbox destination is not an observed reviewed mount")
    marker = sandbox_root / ".DEP01-LOCAL-FAKE"
    if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 128 or marker.read_text(encoding="utf-8").strip() != LOCAL_MARKER:
        raise ValueError("sandbox volume marker mismatch")
    release = _safe_root(Path(session["release_root"]))
    stage = sandbox_root / ("release-" + session["session_id"])
    if stage.exists() or stage.is_symlink() or stage.parent.resolve() != sandbox_root.resolve():
        raise ValueError("stage destination already exists or escapes sandbox")
    if any(item.name != ".DEP01-LOCAL-FAKE" for item in sandbox_root.iterdir()):
        raise ValueError("sandbox must be a dedicated marked empty volume")
    # Preflight all source entries before the first write.
    manifest_path = Path(session["lineage"]["manifest"]["path"])
    manifest = _json(manifest_path)
    entries = manifest.get("files", {})
    _validated_entries(release, entries)
    for rel in entries:
        pure = PurePosixPath(rel)
        dest = stage.joinpath(*pure.parts)
        if not dest.resolve(strict=False).is_relative_to(stage.resolve(strict=False)):
            raise ValueError("stage path escapes sandbox")
    install_state = _install_path(session)
    if install_state.exists() or install_state.is_symlink():
        raise ValueError("candidate installation/session already consumed")
    fd = os.open(install_state, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(json.dumps({"session_id": session["session_id"], "stage": str(stage)}) + "\n")
        stream.flush(); os.fsync(stream.fileno())
    stage.mkdir(mode=0o700)
    try:
        for rel, entry in entries.items():
            source = release.joinpath(*PurePosixPath(rel).parts)
            target = stage.joinpath(*PurePosixPath(rel).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target, follow_symlinks=False)
            os.chmod(target, 0o600)
            if _sha(target) != entry["sha256"]:
                raise ValueError("staged file hash mismatch")
        wheel_target = stage / "artifacts" / "vibetrading.whl"
        wheel_target.parent.mkdir(mode=0o700, exist_ok=True)
        shutil.copyfile(Path(session["anchors"]["wheel_path"]), wheel_target, follow_symlinks=False)
        os.chmod(wheel_target, 0o600)
        if _sha(wheel_target) != session["anchors"]["wheel_sha256"]:
            raise ValueError("staged wheel hash mismatch")
        receipt = {"schema_version": SCHEMA, "session_id": session["session_id"],
                   "frozen_sha": session["frozen_sha"], "manifest_sha256": session["manifest_sha256"],
                   "status": "INSTALLED_BLOCKED", "roles_started": False,
                   "timestamp_ns": time.time_ns(), "files": len(entries),
                   "wheel_sha256": session["anchors"]["wheel_sha256"],
                   "lineage": session["lineage"], "gates": session["gates"],
                   "roles": session["roles"],
                   "source_import_origins": session["source_import_origins"],
                   "operational_evidence": operational}
        tmp = stage / ".release_verification.json.tmp"
        tmp.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, stage / "release_verification.json")
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        p0 = _p0_path(session)
        if not p0.exists():
            try:
                latch_p0(session, "staging integrity fault")
            except (OSError, ValueError):
                pass
        raise
    result = {**session, "operational_evidence": operational,
              "install": {"state": "INSTALLED_BLOCKED", "stage": str(stage)},
              "mutation_count": len(entries) + 4, "roles_started": False}
    result["session_sha256"] = _seal(result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "preflight", "install-blocked", "verify", "activate", "rollback"))
    parser.add_argument("--session", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--release-root", type=Path)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--wheel-sha256")
    parser.add_argument("--host-facts", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sandbox-root", type=Path)
    parser.add_argument("--local-fake-marker")
    args = parser.parse_args(argv)
    try:
        if args.mode == "plan" and args.manifest:
            if not (args.manifest_sha256 and args.release_root and args.wheel and args.wheel_sha256):
                raise ValueError("plan creation requires manifest trust anchor, release root, and wheel digest")
            facts = _json(args.host_facts) if args.host_facts else None
            result = create_session(args.manifest, args.manifest_sha256, args.release_root,
                                    args.output.parent if args.output else args.release_root,
                                    wheel_path=args.wheel, expected_wheel_sha256=args.wheel_sha256,
                                    host_facts=facts)
        else:
            if not args.session:
                raise ValueError("mode requires a previously sealed --session (plan may create one)")
            session = _load_session(_json(args.session))
            result = run_mode(session, args.mode, sandbox_root=args.sandbox_root,
                              local_fake_marker=args.local_fake_marker)
        raw = json.dumps(result, sort_keys=True, indent=2) + "\n"
        if args.output:
            output = _no_symlink_path(args.output)
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        else:
            sys.stdout.write(raw)
        return 0
    except Exception as exc:
        print(f"DEP-01 blocked: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
