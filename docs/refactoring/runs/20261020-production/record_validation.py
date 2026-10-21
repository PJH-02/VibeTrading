"""Bind saved offline test receipts to a frozen source set (stdlib only)."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate")
    parser.add_argument("--windows", required=True)
    parser.add_argument("--linux", required=True)
    parser.add_argument("--output", help="Optional JSON receipt filename in this run directory")
    args = parser.parse_args()
    run = Path(__file__).resolve().parent
    root = run.parents[3]
    proof_path = run / f"{args.candidate}-source.json"
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    source = Path(proof["source"])
    files = proof["files"]
    expected_set = hashlib.sha256(json.dumps(
        files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if expected_set != proof["source_set_sha256"]:
        raise ValueError("Source manifest digest mismatch")
    for name, digest in files.items():
        if sha(source / name) != digest:
            raise ValueError(f"Frozen source changed: {name}")
    reports = {}
    for platform, label, result_name in (
        ("windows", args.windows, "result.json"),
        ("linux", args.linux, "linux_result.json"),
    ):
        directory = run / label
        report = json.loads((directory / result_name).read_text(encoding="utf-8"))
        if report["exit_code"] != 0:
            raise ValueError(f"{platform}: test process failed")
        reported_source = report.get("source_prepared", report["source"])
        normalized_source = str(reported_source).replace("\\", "/").replace("/mnt/c/", "C:/")
        if normalized_source.casefold() != source.as_posix().casefold():
            raise ValueError(f"{platform}: wrong source copy")
        for name, digest in report["tested_source_hashes"].items():
            if files.get(name) != digest:
                raise ValueError(f"{platform}: tested hash mismatch: {name}")
        if report["guard_sha256"] != sha(run / "offline_guard.py"):
            raise ValueError(f"{platform}: guard changed")
        suites = list(ET.parse(directory / "junit.xml").getroot().iter("testsuite"))
        counts = {key: sum(int(s.get(key, "0")) for s in suites)
                  for key in ("tests", "failures", "errors", "skipped")}
        if counts["failures"] or counts["errors"]:
            raise ValueError(f"{platform}: JUnit failures")
        counts["passed"] = counts["tests"] - counts["skipped"]
        receipts = [json.loads(line) for line in
                    (directory / "guard_receipts.jsonl").read_text(encoding="utf-8").splitlines()
                    if line.strip()]
        if not receipts:
            raise ValueError(f"{platform}: missing guard receipts")
        reports[platform] = {
            "status": "OFFLINE_TEST_PASS", "counts": counts,
            "command": report["command"],
            "elapsed_seconds": report["elapsed_seconds"],
            "tested_hash_count": len(report["tested_source_hashes"]),
            "unrecorded_source_paths": sorted(set(files) - set(report["tested_source_hashes"])),
            "hash_coverage_scope": "Only hashes recorded by the launcher are historical test proof; frozen copy rehashed at recording time",
            "guard_receipt_count": len(receipts),
            "artifacts": {p.relative_to(root).as_posix(): sha(p) for p in (
                directory / result_name, directory / "junit.xml",
                directory / "guard_receipts.jsonl", directory / "stdout.txt")},
        }
    output = {
        "schema_version": 1, "candidate": args.candidate,
        "git_head": proof["git_head"], "source_set_sha256": expected_set,
        "source_manifest": proof_path.relative_to(root).as_posix(),
        "source_manifest_sha256": sha(proof_path), "file_count": len(files),
        "platforms": reports,
        "scope": "Credential-free copies with cooperative guard; not an OS sandbox or production proof",
        "production_ready": False, "live_active": False,
        "open_gates": ["server_source_parity", "account_owner_legacy_fencing",
                       "full_checkpoint_and_continuous_input_recovery",
                       "venue_transport_and_private_lifecycle",
                       "actual_live_input_paper", "early_latency_and_capacity",
                       "30min_nats_free", "24h_cloud_outage", "72h_stability", "cutover"],
    }
    if args.output and (Path(args.output).name != args.output or not args.output.endswith('.json')):
        raise ValueError('Output must be a JSON filename')
    output_path = run / (args.output or f"{args.candidate}-validation.json")
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": output_path.relative_to(root).as_posix(),
                      "platforms": {k: v["counts"] for k, v in reports.items()}}))


if __name__ == "__main__":
    main()
