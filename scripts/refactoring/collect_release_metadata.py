"""Render a local, read-only release role report; remote collection is opt-in."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def collect_local(snapshot_path: Path) -> dict:
    """Summarize a supplied historical snapshot without contacting any host."""
    try:
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError(f"local metadata snapshot invalid: {snapshot_path.name}") from None
    if not isinstance(data, dict):
        raise ValueError(f"local metadata snapshot shape invalid: {snapshot_path.name}")
    containers = data.get("containers", [])
    if not isinstance(containers, list):
        raise ValueError("snapshot containers must be a list")
    return {"state": "HISTORICAL_UNVERIFIED_CURRENT_DEPLOY_BLOCKED",
            "observed_utc": data.get("observed_utc"),
            "roles": [{"name": item.get("name"), "image": item.get("image"),
                       "image_id": item.get("image_id"), "command": item.get("command"),
                       "compose_files": item.get("compose_files"), "mounts": item.get("mounts"),
                       "mapping": "UNVERIFIED_HISTORICAL"} for item in containers]}


def collect_external() -> dict:
    """Explicitly requested legacy external probe; never called by default."""
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from scripts.refactoring.collect_korean_hotfix_metadata import REMOTE
    code = REMOTE.replace("NAMES=('vibetrading-etf-mock-1','vibetrading-etf-csv-sync-1')",
                          "NAMES=('vibetrading-storage-1','vibetrading-crypto-storage-1')")
    result = subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=8',
                             '49.50.141.106','python3','-'], input=code,
                            text=True, encoding='utf-8', capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError("external metadata collection failed; output suppressed")
    return json.loads(result.stdout)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True,
                        help="local read-only snapshot; default operation performs no SSH")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--external", action="store_true", help="explicitly invoke legacy remote collection")
    args = parser.parse_args(argv)
    report = collect_external() if args.external else collect_local(args.snapshot)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "historical_roles": len(report["roles"]), "state": report["state"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
