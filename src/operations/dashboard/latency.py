"""Bounded, process-local, best-effort runtime latency observations."""
from __future__ import annotations

import asyncio
import fcntl
from collections import deque
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid

SCHEMA_VERSION = 1
_MAX_KEYS = 128
_MAX_STRATEGIES = 100
_MAX_SAMPLES_PER_KEY = 901
_SAMPLE_PERIOD_NS = 1_000_000_000
_MAX_LABEL_BYTES = 100
_MAX_STAGE_BYTES = 80
_MAX_ID_BYTES = 4096
_MAX_SNAPSHOT_BYTES = 256 * 1024
_STALE_AFTER_NS = 15_000_000_000
_WINDOWS_NS = {"1m": 60_000_000_000, "5m": 300_000_000_000,
               "15m": 900_000_000_000}


def _fresh_process_state():
    global _PID, _PROCESS_ID, _CLOCK_DOMAIN, _LOCK, _metrics, _strategies, _drop_totals, _export_stats
    _PID = os.getpid()
    _PROCESS_ID = uuid.uuid4().hex
    _CLOCK_DOMAIN = "monotonic_ns:" + _PROCESS_ID
    _LOCK = threading.Lock()
    _metrics = {}
    _strategies = {}
    _drop_totals = {"lock_contention": 0, "cardinality": 0, "invalid": 0}
    _export_stats = {"export_errors": 0, "export_oversize_errors": 0,
                     "export_write_errors": 0, "export_lock_errors": 0}


_fresh_process_state()
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_fresh_process_state)


def _ensure_process():
    if os.getpid() != _PID:
        _fresh_process_state()


def telemetry_instance_id(role, role_identity, configured_id=None):
    """Return a stable opaque file component for one configured runtime role."""
    role = role if role in {"collector", "engine"} else "runtime"
    identity = role_identity if isinstance(role_identity, str) else str(role_identity)
    configured = configured_id if isinstance(configured_id, str) else ""
    digest = sha256((role + "\0" + identity + "\0" + configured).encode("utf-8", "replace")).hexdigest()[:24]
    return role + "-" + digest


def _label(value):
    if not isinstance(value, str) or not value or len(value.encode("utf-8", "replace")) > _MAX_LABEL_BYTES:
        return None
    if any(not (ch.isalnum() or ch in "-_.:") for ch in value):
        return None
    lowered=value.lower()
    if (len(value) >= 32 and value.isalnum()
            or any(secret in lowered for secret in ("secret", "password", "api_key", "apikey",
                "access_token", "authorization", "credential", "bearer"))):
        return None
    return value


def _scope_token(value):
    if value == "":
        return ""
    if not isinstance(value, str):
        return None
    try:raw=value.encode("utf-8")
    except UnicodeError:return None
    if len(raw) > _MAX_ID_BYTES:return None
    return sha256(raw).hexdigest()[:24]


def _trace_token(value):
    if value == "":
        return ""
    if not isinstance(value, str) or len(value.encode("utf-8", "replace")) > _MAX_ID_BYTES:
        return None
    return sha256(value.encode("utf-8", "replace")).hexdigest()[:24]


def _mode(value):
    if not isinstance(value, str):
        return None
    return {"paper": "paper", "PAPER": "paper", "live": "live", "LIVE": "live",
            "sandbox": "sandbox", "SANDBOX": "sandbox", "testnet": "sandbox",
            "TESTNET": "sandbox", "mock": "mock", "MOCK": "mock",
            "mock2": "mock2", "MOCK2": "mock2"}.get(value)


def _nonnegative_int(value):
    return value if type(value) is int and value >= 0 else None


def observe(stage, *, started_ns, ended_ns, source_id="", strategy_id="",
            account_scope="", mode="", trace_id="", queue_depth=None,
            oldest_queue_age_ns=None, source_age_ns=None, error=False):
    """Sample a stage at most once per second; never waits or raises to caller."""
    try:
        _ensure_process()
        stage, source, strategy = _label(stage), _label(source_id) if source_id else "", _label(strategy_id) if strategy_id else ""
        scope, trace, normalized_mode = _scope_token(account_scope), _trace_token(trace_id), _mode(mode) if mode else ""
        start, end = _nonnegative_int(started_ns), _nonnegative_int(ended_ns)
        now = time.monotonic_ns()
        if (stage is None or len(stage.encode("utf-8")) > _MAX_STAGE_BYTES
                or source is None or strategy is None or scope is None or trace is None
                or normalized_mode is None or start is None or end is None or end < start or end > now):
            _drop_totals["invalid"] += 1
            return False
        key = (stage, source, strategy, scope, normalized_mode)
        if not _LOCK.acquire(False):
            _drop_totals["lock_contention"] += 1
            return False
        try:
            state = _metrics.get(key)
            if state is None:
                if len(_metrics) >= _MAX_KEYS:
                    _drop_totals["cardinality"] += 1
                    return False
                state = {"samples": deque(maxlen=_MAX_SAMPLES_PER_KEY), "last_sample_ns": None,
                         "last_observation_ns": None, "latest_source_age_ns": None,
                         "latest_queue_depth": None, "latest_oldest_queue_age_ns": None,
                         "latest_trace_ref": None, "seen": 0, "sampled": 0,
                         "sample_out": 0, "errors": 0, "drops": 0}
                _metrics[key] = state
            state["seen"] += 1
            state["errors"] += int(bool(error))
            age, depth, oldest = (_nonnegative_int(source_age_ns), _nonnegative_int(queue_depth),
                                  _nonnegative_int(oldest_queue_age_ns))
            state["last_observation_ns"] = end
            state["latest_source_age_ns"] = age
            state["latest_queue_depth"] = depth
            state["latest_oldest_queue_age_ns"] = oldest
            state["latest_trace_ref"] = trace or None
            last = state["last_sample_ns"]
            if last is not None and end - last < _SAMPLE_PERIOD_NS:
                state["sample_out"] += 1
                return True
            if len(state["samples"]) == _MAX_SAMPLES_PER_KEY:
                state["drops"] += 1
            state["samples"].append((end, (end - start) / 1_000_000.0, age, depth, oldest, trace or None))
            state["last_sample_ns"] = end
            state["sampled"] += 1
        finally:
            _LOCK.release()
        return True
    except BaseException:
        return False


def observe_strategy(strategy_id, account_scope, mode, status, *, currency=None):
    """Record lifecycle facts only when the runtime has direct evidence."""
    try:
        _ensure_process()
        sid, state = _label(strategy_id), _label(status)
        scope, run_mode = _scope_token(account_scope), _mode(mode)
        curr = currency.upper() if isinstance(currency, str) and len(currency) <= 16 else None
        if (sid is None or state is None or scope is None or run_mode is None
                or (currency is not None and (curr is None or not curr.isalnum()))):
            _drop_totals["invalid"] += 1
            return False
        key = (sid, scope, run_mode, curr or "")
        if not _LOCK.acquire(False):
            _drop_totals["lock_contention"] += 1
            return False
        try:
            if key not in _strategies and len(_strategies) >= _MAX_STRATEGIES:
                _drop_totals["cardinality"] += 1
                return False
            _strategies[key] = (state, int(time.time() * 1000))
        finally:
            _LOCK.release()
        return True
    except BaseException:
        return False


def _percentile(values, percentile):
    if not values:
        return None
    values.sort()
    index = max(0, min(len(values) - 1, int((len(values) - 1) * percentile + 0.5)))
    return round(values[index], 3)


def _cgroup_resources():
    """Optional scoped Linux cgroup v2 facts; unavailable remains unknown.

    Called only by the cold snapshot builder, never by observation hooks.
    Fixed file/byte budgets avoid recursive scans or unbounded proc reads.
    """
    result = dict.fromkeys(("cgroup_cpu_seconds", "cgroup_memory_bytes",
                           "cgroup_io_some_avg10", "cgroup_io_full_avg10"))
    try:
        with open("/proc/self/cgroup", encoding="ascii") as stream:
            rows = stream.read(4096).splitlines()
        relative = next(row[3:] for row in rows if row.startswith("0::/"))
        root = Path("/sys/fs/cgroup").resolve()
        group = (root / relative.lstrip("/")).resolve()
        if not group.is_relative_to(root):
            return result
        def read(name):
            with (group / name).open(encoding="ascii") as stream:
                return stream.read(4096)
        try:
            value = int(read("memory.current").strip())
            if value >= 0: result["cgroup_memory_bytes"] = value
        except (OSError, ValueError):
            pass
        try:
            cpu = dict(line.split() for line in read("cpu.stat").splitlines())
            value = int(cpu["usage_usec"])
            if value >= 0: result["cgroup_cpu_seconds"] = value / 1_000_000
        except (OSError, ValueError, KeyError):
            pass
        try:
            for row in read("io.pressure").splitlines():
                kind, *fields = row.split()
                if kind not in {"some", "full"}: continue
                value = float(dict(field.split("=", 1) for field in fields)["avg10"])
                if math.isfinite(value) and value >= 0:
                    result["cgroup_io_" + kind + "_avg10"] = value
        except (OSError, ValueError, KeyError):
            pass
    except (OSError, ValueError, StopIteration):
        pass
    return result


def _resource_snapshot():
    cpu_seconds = time.process_time()
    rss_bytes = None
    try:
        with open("/proc/self/statm", "r", encoding="ascii") as statm:
            rss_bytes = int(statm.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        pass
    return {"pid": os.getpid(), "process_cpu_seconds": round(cpu_seconds, 6), "rss_bytes": rss_bytes, **_cgroup_resources()}


def _record_export_error(kind):
    _ensure_process()
    if not _LOCK.acquire(False):
        return
    try:
        _export_stats["export_errors"] += 1
        _export_stats[kind] += 1
    finally:
        _LOCK.release()


def snapshot():
    """Build a validated bounded public snapshot; peer clocks stay segmented."""
    _ensure_process()
    now, wall_ms = time.monotonic_ns(), int(time.time() * 1000)
    try:
        with _LOCK:
            captured = [(key, tuple(state["samples"]), dict(state)) for key, state in _metrics.items()]
            strategies = list(_strategies.items())
            drop_totals = dict(_drop_totals)
            export_stats = dict(_export_stats)
        metrics = []
        for key, records, counters in captured:
            windows = {}
            selected = []
            for name, span in _WINDOWS_NS.items():
                rows = [row for row in records if 0 <= now - row[0] <= span]
                durations = [row[1] for row in rows]
                coverage = min(span, now - rows[0][0]) / 1_000_000 if rows else 0
                windows[name] = {"p50_ms": _percentile(durations, .50),
                                 "p95_ms": _percentile(durations, .95),
                                 "p99_ms": _percentile(durations, .99),
                                 "n": len(rows), "coverage_ms": round(coverage, 3)}
                if name == "15m":
                    selected = rows
            latest = selected[-1] if selected else None
            last_observation_ns=counters["last_observation_ns"]
            last_observation_age_ns=max(0,now-last_observation_ns) if last_observation_ns is not None else None
            last_observation_age_ms=(last_observation_age_ns/1_000_000
                                     if last_observation_age_ns is not None else None)
            latest_source_age_ms=(counters["latest_source_age_ns"]/1_000_000+last_observation_age_ms
                                 if counters["latest_source_age_ns"] is not None and last_observation_age_ms is not None else None)
            latest_oldest_age_ms=(counters["latest_oldest_queue_age_ns"]/1_000_000+last_observation_age_ms
                                  if counters["latest_oldest_queue_age_ns"] is not None and last_observation_age_ms is not None else None)
            metrics.append({"stage": key[0], "source_id": key[1], "strategy_id": key[2],
                "account_scope": key[3], "mode": key[4], "clock_domain": _CLOCK_DOMAIN,
                "trace_ref": counters["latest_trace_ref"], "windows": windows,
                "source_latest_age_ms": latest_source_age_ms,
                "last_observation_age_ms": last_observation_age_ms,
                "queue_depth": counters["latest_queue_depth"],
                "oldest_queue_age_ms": latest_oldest_age_ms,
                "errors": counters["errors"], "drops": counters["drops"],
                "seen": counters["seen"], "sampled": counters["sampled"],
                "sample_out": counters["sample_out"],
                "stale": last_observation_age_ns is None or last_observation_age_ns > _STALE_AFTER_NS})
        resources = _resource_snapshot()
        resources.update({"sample_capacity_per_metric": _MAX_SAMPLES_PER_KEY,
            "metric_capacity": _MAX_KEYS, "lock_contention_drops": drop_totals["lock_contention"],
            "cardinality_drops": drop_totals["cardinality"], "invalid_drops": drop_totals["invalid"],
            **export_stats,
            "sampling_period_ms": 1000, "retention_ms": 900000})
        return {"schema_version": SCHEMA_VERSION, "kind": "runtime_latency",
            "process_id": _PROCESS_ID, "clock_domain": _CLOCK_DOMAIN, "generated_ms": wall_ms,
            "metrics": metrics, "strategies": [{"strategy_id": key[0], "account_scope": key[1],
                "mode": key[2], "currency": key[3] or None, "status": value[0], "updated_ms": value[1]}
                for key, value in strategies], "end_to_end_ms": None,
            "end_to_end_status": "SEGMENTED", "resources": resources}
    except BaseException:
        return {"schema_version": SCHEMA_VERSION, "kind": "runtime_latency", "process_id": _PROCESS_ID,
            "clock_domain": _CLOCK_DOMAIN, "generated_ms": wall_ms, "metrics": [], "strategies": [],
            "end_to_end_ms": None, "end_to_end_status": "SEGMENTED", "resources": {"snapshot_error": True}}


async def export_metrics(directory, *, stop=None, interval_seconds=2, instance_id=None):
    """Keep all monitor I/O on one daemon thread, never on the trading loop.

    Shutdown signals the writer without joining a potentially stalled filesystem.
    The instance lock prevents a restarted exporter from competing with it.
    """
    cadence = max(1.0, min(5.0, float(interval_seconds)))
    root = Path(directory)
    if instance_id is None:
        instance_id = "process-" + _PROCESS_ID
    if (not isinstance(instance_id, str) or not 1 <= len(instance_id) <= 64
            or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-" for ch in instance_id)):
        raise ValueError("invalid telemetry instance id")
    if stop is not None and stop.is_set():
        return
    halted = threading.Event()
    done = asyncio.Event()
    loop = asyncio.get_running_loop()

    def writer():
        try:
            if not root.is_dir():
                _record_export_error("export_write_errors")
                return
            if halted.is_set():
                return
            target = root / ("runtime-latency-" + instance_id + ".json")
            lock_path = root / ("runtime-latency-" + instance_id + ".lock")
            with lock_path.open("a+b") as lock_file:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    _record_export_error("export_lock_errors")
                    return
                while not halted.is_set() and (stop is None or not stop.is_set()):
                    try:
                        payload = _encode_snapshot()
                        if len(payload) > _MAX_SNAPSHOT_BYTES:
                            _record_export_error("export_oversize_errors")
                        elif not halted.is_set():
                            _atomic_write(target, payload)
                    except Exception:
                        _record_export_error("export_write_errors")
                    if stop is not None and stop.is_set():
                        break
                    halted.wait(cadence)
        except Exception:
            _record_export_error("export_write_errors")
        finally:
            try:
                loop.call_soon_threadsafe(done.set)
            except RuntimeError:
                pass

    thread = threading.Thread(target=writer, name="dashboard-latency-io", daemon=True)
    waits = []
    try:
        thread.start()
        waits.append(asyncio.create_task(done.wait()))
        if stop is not None:
            waits.append(asyncio.create_task(stop.wait()))
        await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
    finally:
        halted.set()
        for waiter in waits:
            waiter.cancel()


def _atomic_write(target, payload):
    temp = target.with_suffix(".tmp")
    with temp.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, target)


def _encode_snapshot():
    return json.dumps(snapshot(), separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _valid_number(value, *, nullable=True):
    return value is None and nullable or type(value) in (int, float) and math.isfinite(value) and value >= 0


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _validate_snapshot(data, now, max_age_ms):
    top = {"schema_version", "kind", "process_id", "clock_domain", "generated_ms", "metrics",
           "strategies", "end_to_end_ms", "end_to_end_status", "resources"}
    if type(data) is not dict or set(data) != top:
        return None
    pid, domain = data["process_id"], data["clock_domain"]
    if (type(data["schema_version"]) is not int or data["schema_version"] != 1
            or data["kind"] != "runtime_latency" or type(pid) is not str
            or not 1 <= len(pid) <= 64 or domain != "monotonic_ns:" + pid
            or type(data["generated_ms"]) is not int or data["generated_ms"] < 0
            or type(data["metrics"]) is not list or len(data["metrics"]) > _MAX_KEYS
            or type(data["strategies"]) is not list or len(data["strategies"]) > _MAX_STRATEGIES
            or data["end_to_end_ms"] is not None or data["end_to_end_status"] != "SEGMENTED"):
        return None
    metrics = []
    metric_fields = {"stage", "source_id", "strategy_id", "account_scope", "mode", "clock_domain",
        "trace_ref", "windows", "source_latest_age_ms", "last_observation_age_ms", "queue_depth", "oldest_queue_age_ms",
        "errors", "drops", "seen", "sampled", "sample_out", "stale"}
    for row in data["metrics"]:
        if type(row) is not dict or set(row) != metric_fields:
            return None
        if (row["clock_domain"] != domain or _label(row["stage"]) is None
                or len(row["stage"].encode("utf-8")) > _MAX_STAGE_BYTES
                or any(row[k] != "" and _label(row[k]) is None for k in ("source_id", "strategy_id"))
                or row["mode"] not in {"", "paper", "sandbox", "live", "mock", "mock2"}
                or row["account_scope"] != "" and (type(row["account_scope"]) is not str or len(row["account_scope"]) != 24
                    or any(c not in "0123456789abcdef" for c in row["account_scope"]))
                or row["trace_ref"] is not None and (type(row["trace_ref"]) is not str or len(row["trace_ref"]) != 24
                    or any(c not in "0123456789abcdef" for c in row["trace_ref"]))
                or any(type(row[k]) is not int or row[k] < 0 for k in ("errors", "drops", "seen", "sampled", "sample_out"))
                or row["errors"] > row["seen"] or row["sampled"] + row["sample_out"] > row["seen"]
                or type(row["stale"]) is not bool or row["queue_depth"] is not None
                    and (type(row["queue_depth"]) is not int or row["queue_depth"] < 0)
                or not _valid_number(row["source_latest_age_ms"]) or not _valid_number(row["last_observation_age_ms"])
                or not _valid_number(row["oldest_queue_age_ms"])):
            return None
        if type(row["windows"]) is not dict or set(row["windows"]) != set(_WINDOWS_NS):
            return None
        clean_windows = {}
        for name, window in row["windows"].items():
            if (type(window) is not dict or set(window) != {"p50_ms", "p95_ms", "p99_ms", "n", "coverage_ms"}
                    or any(not _valid_number(window[k]) for k in ("p50_ms", "p95_ms", "p99_ms"))
                    or type(window["n"]) is not int or window["n"] < 0
                    or not _valid_number(window["coverage_ms"], nullable=False)
                    or window["coverage_ms"] > _WINDOWS_NS[name] / 1_000_000
                    or window["n"] > _MAX_SAMPLES_PER_KEY
                    or (window["n"] == 0 and any(window[k] is not None for k in ("p50_ms", "p95_ms", "p99_ms")))
                    or (window["n"] > 0 and any(window[k] is None for k in ("p50_ms", "p95_ms", "p99_ms")) )):
                return None
            clean_windows[name] = dict(window)
        metrics.append({**row, "windows": clean_windows})
    strategies = []
    for row in data["strategies"]:
        fields = {"strategy_id", "account_scope", "mode", "currency", "status", "updated_ms"}
        if type(row) is not dict or set(row) != fields:
            return None
        if (any(_label(row[k]) is None for k in ("strategy_id", "status")) or row["mode"] not in {"paper", "sandbox", "live", "mock", "mock2"}
                or type(row["updated_ms"]) is not int or row["updated_ms"] < 0
                or row["currency"] is not None and (type(row["currency"]) is not str or len(row["currency"]) > 16
                    or not row["currency"].isalnum())
                or row["account_scope"] != "" and (type(row["account_scope"]) is not str or len(row["account_scope"]) != 24
                    or any(c not in "0123456789abcdef" for c in row["account_scope"]))):
            return None
        strategies.append(dict(row))
    res = data["resources"]
    allowed_res = {"pid", "process_cpu_seconds", "rss_bytes", "sample_capacity_per_metric", "metric_capacity",
        "lock_contention_drops", "cardinality_drops", "invalid_drops", "sampling_period_ms", "retention_ms",
        "export_errors", "export_oversize_errors", "export_write_errors", "export_lock_errors",
        "cgroup_cpu_seconds", "cgroup_memory_bytes", "cgroup_io_some_avg10", "cgroup_io_full_avg10"}
    optional_res = {"cgroup_cpu_seconds", "cgroup_memory_bytes", "cgroup_io_some_avg10", "cgroup_io_full_avg10"}
    if (type(res) is not dict or set(res) != allowed_res
            or type(res["pid"]) is not int or res["pid"] <= 0
            or not _valid_number(res["process_cpu_seconds"], nullable=False)
            or res["rss_bytes"] is not None and (type(res["rss_bytes"]) is not int or res["rss_bytes"] < 0)
            or any(not _valid_number(res[k]) for k in optional_res)
            or any(type(res[k]) is not int or res[k] < 0 for k in allowed_res - optional_res - {"process_cpu_seconds", "rss_bytes"})):
        return None
    stale = abs(now - data["generated_ms"]) > max_age_ms
    return {"schema_version": 1, "kind": "runtime_latency", "process_id": pid,
        "clock_domain": domain, "generated_ms": data["generated_ms"], "metrics": metrics,
        "strategies": strategies, "end_to_end_ms": None, "end_to_end_status": "SEGMENTED",
        "resources": dict(res), "snapshot_stale": stale}


def read_latency_snapshots(paths, *, now_ms=None, max_age_ms=15000):
    """Read explicit bounded files, strict-allowlist fields, and retain stale status."""
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    if not isinstance(paths, (list, tuple)):
        return []
    accepted = []
    for path in paths[:32]:
        try:
            p = Path(path)
            if not p.is_absolute() or not p.is_file() or p.is_symlink() or p.stat().st_size > _MAX_SNAPSHOT_BYTES:
                continue
            with p.open("rb") as stream:
                raw = stream.read(_MAX_SNAPSHOT_BYTES + 1)
            if len(raw) > _MAX_SNAPSHOT_BYTES:
                continue
            result = _validate_snapshot(json.loads(raw, object_pairs_hook=_unique_object), now, max_age_ms)
            if result is not None:
                accepted.append(result)
        except BaseException:
            continue
    return accepted
