"""Summarize paired input profiles without promoting synthetic timing to a gate."""
import hashlib
import json
from pathlib import Path
import re
import statistics

run = Path(__file__).resolve().parent
groups = {
    "windows": {"before": ["vn05-probe-windows-before-a", "vn05-probe-windows-before-c"],
                "after": ["vn05-probe-windows-after-b", "vn05-probe-windows-after-d"]},
    "linux": {"before": ["vn05-probe-linux-before-a"], "after": ["vn05-probe-linux-after-b"]},
}
sources = {stage: json.loads((run / f"production-v{version}-source.json").read_text(encoding="utf-8"))
           for stage, version in (("before", 6), ("after", 7))}
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
report = {"schema_version": 1, "date_kst": "2026-10-21",
          "harness_sha256": sha(run / "group_sync_probe.py"),
          "sources": {k: v["source_set_sha256"] for k, v in sources.items()},
          "platforms": {}, "production_performance_gate": "OPEN_MISSING_EVIDENCE",
          "limitations": ["Real local fsync with fake ACK; no engine/strategy/account/NATS/venue",
                          "Alternating before/after warm-state runs; cold-cache and instrumentation overhead not measured",
                          "Small synthetic input; p99.9 not evaluated, no production latency/capacity or 20% CPU claim",
                          "Windows native disk and Linux WSL /tmp are different filesystems; never pool across platforms"]}
for platform, stages in groups.items():
    profiles = {}
    artifacts = {}
    for stage, labels in stages.items():
        rows = []
        for label in labels:
            directory = run / label
            result_name = "result.json" if platform == "windows" else "linux_result.json"
            result = json.loads((directory / result_name).read_text(encoding="utf-8"))
            assert result["exit_code"] == 0
            records = [json.loads(m) for m in re.findall(
                r"VN05_PROBE=(\{[^\n]+\})", (directory / "stdout.txt").read_text(encoding="utf-8"))]
            assert len(records) == 6
            assert sorted((r["profile"], r["repeat"]) for r in records) == [
                (p, i) for p in ("burst8", "sparse1") for i in range(3)]
            expected = sources[stage]["files"]["vibetrading/local_pipeline.py"]
            assert all(r["pipeline_sha256"] == expected for r in records)
            rows.extend(records)
            artifacts[label] = {n: sha(directory / n) for n in (result_name, "stdout.txt", "junit.xml", "guard_receipts.jsonl")}
        for profile in ("burst8", "sparse1"):
            selected = [r for r in rows if r["profile"] == profile]
            samples = sorted(n for r in selected for n in r["raw_admission_to_fake_send_ns"])
            assert all(len(r["raw_admission_to_fake_send_ns"]) == r["events"] for r in selected)
            summary = {"runs": len(selected), "events": len(samples),
                       "mean_ms": statistics.mean(samples) / 1e6,
                       "p50_ms": samples[int((len(samples)-1)*.5)] / 1e6,
                       "p95_ms": samples[int((len(samples)-1)*.95)] / 1e6,
                       "p99_ms": samples[int((len(samples)-1)*.99)] / 1e6,
                       "repeat_p99_ms_range": [min(r["admission_to_fake_send_ms"]["p99"] for r in selected),
                                               max(r["admission_to_fake_send_ms"]["p99"] for r in selected)],
                       "mean_process_cpu_ns_per_event": statistics.mean(r["process_cpu_ns_per_event"] for r in selected)}
            profiles.setdefault(profile, {})[stage] = summary
    for values in profiles.values():
        values["after_over_before_p99_ratio"] = values["after"]["p99_ms"] / values["before"]["p99_ms"]
    report["platforms"][platform] = {"profiles": profiles, "artifacts": artifacts}
(run / "vn05-timing-summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({p: v["profiles"] for p, v in report["platforms"].items()}, indent=2))
