"""Small local 2-CPU CAP-04 pipeline measurement (synthetic PAPER only).

Run through the private CAP04 test sandbox by collecting
`tests/refactoring/test_cap04_adverse.py::test_two_cpu_spool_strategy_matcher_benchmark`.
The reported T0->T5 is producer append through durable spool, shared HotPath,
isolated healthy strategy, parent action validation and PaperExecution/WAL.
It is not a venue adapter/network T5 measurement.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import random
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
TEST_HELPERS = ROOT / "tests" / "refactoring"
sys.path.insert(0, str(TEST_HELPERS))


def _percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    rank = max(1, int((len(ordered) * fraction) + .999999))
    return ordered[min(len(ordered) - 1, rank - 1)]


def _bootstrap_interval(values, fraction=.99, repetitions=500):
    if not values:
        return None
    randomizer = random.Random(41004)
    estimates = []
    for _ in range(repetitions):
        sample = [values[randomizer.randrange(len(values))] for _ in values]
        estimates.append(_percentile(sample, fraction))
    return [_percentile(estimates, .025), _percentile(estimates, .975)]


def run_benchmark(samples=1000, *, action_every=25):
    import test_cap04_isolation as h
    from runtime.strategy_process import IsolationBudgets, StrategyIsolation
    from strategies.contract import load_strategy_manifest
    from storage.market_spool import MarketSpool
    from execution.paper_execution import PaperExecution

    if type(samples) is not int or not 20 <= samples <= 1000:
        raise ValueError("benchmark sample count must be explicitly bounded to 20..1000")
    if type(action_every) is not int or not 10 <= action_every <= 100:
        raise ValueError("action_every must be explicitly bounded to 10..100")
    action_count = samples // action_every
    if action_count > 100:
        raise ValueError("benchmark action count exceeds the synthetic PAPER ledger budget")

    allowed = sorted(os.sched_getaffinity(0))
    if len(allowed) < 2:
        raise RuntimeError("CAP04 benchmark requires at least two allowed CPUs")
    chosen = set(allowed[:2])
    previous = set(allowed)
    os.sched_setaffinity(0, chosen)
    try:
        with tempfile.TemporaryDirectory(prefix="cap04-bench-") as td:
            root = Path(td)
            ledger = h.ledger_at(root)
            host = None
            spool = None
            try:
                ledger.seed_balance("USDT", h.D(100000), {"source": "synthetic"})
                matcher, now = PaperExecution(ledger, entry_latency_ns=10,
                    cancel_latency_ns=5, max_open_orders=samples + 4, max_depth=8,
                    max_receipts=samples * 4, max_state_bytes=500_000,
                    stale_after_ns=1_000_000_000), [h.T]
                isolation = StrategyIsolation(
                    budgets=IsolationBudgets(callback_timeout_ns=1_000_000_000,
                        max_queue_frames=8, max_queue_bytes=1_048_576),
                    allowed_source_roots=(str(root),),
                    readonly_paths=(__import__("site").getusersitepackages(),),
                    require_bubblewrap=True)
                host = h.runtime(ledger, matcher, now, isolation=isolation)
                source = h.write_plugin(root)
                source_text = source.read_text(encoding="utf-8")
                source_text = source_text.replace(
                    "        self.seen += 1\n",
                    "        self.seen += 1\n"
                    f"        if self.seen % {action_every} != 0: return []\n")
                source_text = source_text.replace(
                    "valid_until_ts_ns=event.receive_ts_ns+100000",
                    "valid_until_ts_ns=event.receive_ts_ns+1000000000")
                source.write_text(source_text, encoding="utf-8")
                manifest = load_strategy_manifest(source.stem, (str(root),))
                process_start_ns = time.perf_counter_ns()
                host.register(h.registration(ledger, manifest))
                init_deadline_ns = time.monotonic_ns() + 5_000_000_000
                while not host._jobs[manifest.strategy_id].isolated_initialized:
                    host.poll_isolated()
                    if time.monotonic_ns() > init_deadline_ns:
                        raise RuntimeError("isolated strategy cold initialization exceeded its bound")
                    time.sleep(.001)
                cold_start_ns = time.perf_counter_ns() - process_start_ns
                spool = MarketSpool(root / "source-spool", "bench-producer", "bench-run",
                    max_disk_bytes=4_000_000, max_records=samples + 16,
                    max_frame_bytes=256_000, max_header_bytes=1024,
                    segment_max_bytes=1_000_000, segment_max_records=samples + 8,
                    segment_max_age_seconds=3600)
                action_durations = []
                no_action_durations = []
                stale_rejections = []
                queue_ages = []
                observed_requests = set()
                for index in range(samples):
                    event_now = time.time_ns()
                    now[0] = event_now
                    event = h.book(event_now).model_copy(update={
                        "event_id": f"bench-book-{index}", "sequence": index + 1,
                        "final_update_id": index + 1, "sync_generation": 1,
                        "receive_ts_ns": event_now,
                        "wallclock_ts_ns": event_now,
                        "source_ts_ns": event_now})
                    started = time.perf_counter_ns()
                    receipt = spool.append(json.dumps(event.model_dump(mode="json"),
                        sort_keys=True, separators=(",", ":")).encode())
                    durable = spool.flush()
                    if durable.sequence != receipt.cursor.sequence:
                        raise RuntimeError("spool durable cursor failed the A1 benchmark barrier")
                    callback_target = host._jobs[manifest.strategy_id].callbacks + 1
                    action_expected = (callback_target % action_every) == 0
                    order_target = len(ledger.report()["orders"]) + int(action_expected)
                    host.process_event(event)
                    job = host._jobs[manifest.strategy_id]
                    input_reason = host.report()["invalid_inputs"].get(h.BTC.key)
                    if (job.state == "WARMING" and job.reason == "input_invalid"
                            and input_reason in {"receipt_or_native_source_not_fresh",
                                                "cached_book_not_fresh"}):
                        stale_rejections.append({"sample": index + 1, "reason": input_reason,
                            "age_ns": max(0, time.time_ns() - event.receive_ts_ns)})
                        continue
                    callback_deadline_ns = time.monotonic_ns() + 2_000_000_000
                    for _ in range(500):
                        now[0] = time.time_ns()
                        worker = host._jobs[manifest.strategy_id].worker
                        pending = worker._pending
                        if (pending is not None and pending.kind in {"event", "feedback"}
                                and pending.request_id not in observed_requests):
                            observed_requests.add(pending.request_id)
                            queue_ages.append(max(0, time.monotonic_ns() - pending.enqueued_ns))
                        host.poll_isolated()
                        if (host._jobs[manifest.strategy_id].callbacks >= callback_target
                                and len(ledger.report()["orders"]) >= order_target):
                            break
                        if time.monotonic_ns() > callback_deadline_ns:
                            break
                        time.sleep(0.001)
                    if (job.callbacks < callback_target
                            and job.state == "WARMING" and job.reason == "input_invalid"):
                        input_reason = host.report()["invalid_inputs"].get(h.BTC.key)
                        if input_reason in {"receipt_or_native_source_not_fresh",
                                            "cached_book_not_fresh"}:
                            stale_rejections.append({"sample": index + 1,
                                "reason": input_reason,
                                "age_ns": max(0, time.time_ns() - event.receive_ts_ns)})
                            continue
                    if (job.callbacks < callback_target
                            or len(ledger.report()["orders"]) < order_target):
                        current = time.time_ns()
                        latest = host.hot_path.snapshot(current, "PAPER")
                        diagnostics = {
                            "job": host.report()["strategies"].get(manifest.strategy_id),
                            "invalid_inputs": host.report().get("invalid_inputs"),
                            "account_faults": host.report().get("account_faults"),
                            "event": {"receive_ts_ns": event.receive_ts_ns,
                                      "source_ts_ns": event.source_ts_ns,
                                      "receive_age_ns": current - event.receive_ts_ns,
                                      "source_age_ns": (current - event.source_ts_ns
                                                        if event.source_ts_ns is not None else None)},
                            "latest_quality": dict(latest.quality),
                            "latest_book": ({"receive_ts_ns": latest.latest_book(h.BTC.key).receive_ts_ns,
                                             "source_ts_ns": latest.latest_book(h.BTC.key).source_ts_ns,
                                             "age_ns": current - latest.latest_book(h.BTC.key).receive_ts_ns}
                                            if latest.latest_book(h.BTC.key) else None),
                            "spool_cursor": spool.durable_cursor.sequence,
                            "hot_path_apply_seq": host.hot_path.apply_seq,
                            "matcher_clock_ns": matcher.report()["clock_ns"],
                        }
                        raise RuntimeError("healthy isolated strategy did not reach Paper matcher/WAL: " +
                            repr(diagnostics))
                    elapsed = time.perf_counter_ns() - started
                    if action_expected:
                        action_durations.append(elapsed)
                    else:
                        no_action_durations.append(elapsed)
                    worker = host._jobs[manifest.strategy_id].worker
                report = host.report()
                worker = host._jobs[manifest.strategy_id].worker
                rss_profile = worker.rss_profile() or {}
                return {
                    "status": "MEASURED_LOCAL_SYNTHETIC_PAPER",
                    "affinity_allowed": allowed, "affinity_selected": sorted(chosen),
                    "cgroup_cpu_max": _read_text("/sys/fs/cgroup/cpu.max"),
                    "cgroup_memory_max": _read_text("/sys/fs/cgroup/memory.max"),
                    "samples": samples, "action_samples": len(action_durations),
                    "no_action_samples": len(no_action_durations),
                    "evaluated_samples": len(action_durations) + len(no_action_durations),
                    "stale_rejected_samples": len(stale_rejections),
                    "stale_rejections": stale_rejections,
                    "cold_worker_start_ns": cold_start_ns,
                    "warm_worker_action_latency_ns": {
                        "p50": _percentile(action_durations, .50),
                        "p95": _percentile(action_durations, .95),
                        "p99": _percentile(action_durations, .99),
                        "p99_bootstrap_ci95": _bootstrap_interval(action_durations),
                        "max": max(action_durations) if action_durations else None},
                    "warm_worker_no_action_evaluation_ns": {
                        "p50": _percentile(no_action_durations, .50),
                        "p95": _percentile(no_action_durations, .95),
                        "p99": _percentile(no_action_durations, .99),
                        "p99_bootstrap_ci95": _bootstrap_interval(no_action_durations),
                        "max": max(no_action_durations) if no_action_durations else None},
                    "durable_spool_sequence": spool.durable_cursor.sequence,
                    "paper_orders": len(ledger.report()["orders"]),
                    "queue_wait_ns": {"samples": len(queue_ages),
                                      "p50": _percentile(queue_ages, .50),
                                      "p95": _percentile(queue_ages, .95),
                                      "max": max(queue_ages) if queue_ages else 0},
                    "queue_depth": worker.queued,
                    "queue_bytes": worker.queue_bytes,
                    "rss_parent_bytes": _rss(os.getpid()),
                    "rss_child_bytes": worker.rss_bytes(),
                    "rss_worker_root_bytes": rss_profile.get("root_bytes"),
                    "rss_worker_descendant_bytes": rss_profile.get("descendant_bytes"),
                    "rss_total_observed_bytes": (_rss(os.getpid()) or 0) + (worker.rss_bytes() or 0),
                    "pipeline_boundary": "spool_append+fsync->HotPath->isolated_strategy->parent_action_validation->PaperExecution/PortfolioLedger_WAL",
                    "venue_adapter_T5": "NOT_MEASURED",
                    "production_claim": "none; synthetic local PAPER fixture only",
                    "runtime_report": report["isolation"],
                }
            finally:
                if spool is not None:
                    spool.close()
                if host is not None:
                    host.close()
                ledger.close()
    finally:
        os.sched_setaffinity(0, previous)


def _rss(pid):
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        return None
    return None


def _read_text(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return "unavailable"


if __name__ == "__main__":
    print(json.dumps(run_benchmark(), sort_keys=True, indent=2))
