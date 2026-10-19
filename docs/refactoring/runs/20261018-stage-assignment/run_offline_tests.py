"""Run tests from a credential-free source copy and preserve exact evidence."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

RUN = Path(__file__).resolve().parent
ROOT = RUN.parents[3]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("tests", nargs="*")
    args = parser.parse_args()
    source = args.source.resolve()
    if not source.is_relative_to(ROOT / "tmp" / "refactoring"):
        raise ValueError("Expected a prepared test source copy under tmp/refactoring")
    if (source / ".env").exists():
        raise RuntimeError("Test source copy must not contain a root .env")
    if not args.label.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid run label")
    result_dir = RUN / args.label
    result_dir.mkdir(exist_ok=False)
    # Keep Arrow's nested partition paths below Windows' legacy path limit.
    temporary = ROOT / "tmp" / ("rf-" + hashlib.sha256(args.label.encode()).hexdigest()[:6])
    temporary.mkdir(exist_ok=True)
    guard = RUN / "offline_guard.py"
    shutil.copy2(guard, source / "sitecustomize.py")
    keep = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "SYSTEMDRIVE", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"}
    environment = {k: v for k, v in os.environ.items() if k.upper() in keep}
    receipt = result_dir / "guard_receipts.jsonl"
    environment.update({
        "PYTHONPATH": str(source), "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "TEMP": str(temporary), "TMP": str(temporary),
        "MPLCONFIGDIR": str(result_dir / "matplotlib"),
        "REFACTOR_GUARD_RECEIPT": str(receipt),
    })
    preflight = "import sys,socket,subprocess; assert getattr(sys,'_vibetrading_offline_guard',False); import asyncio; asyncio.run(asyncio.sleep(0)); "
    check = subprocess.run([sys.executable, "-c", preflight], cwd=source, env=environment, text=True, capture_output=True, timeout=20)
    if check.returncode:
        raise RuntimeError("Offline preflight failed: " + check.stderr[:1000])
    bootstrap = "import sys; assert getattr(sys,'_vibetrading_offline_guard',False); import pytest; raise SystemExit(pytest.main(sys.argv[1:]))"
    command = [sys.executable, "-c", bootstrap, "-p", "pytest_asyncio.plugin", "-q", "-m", "not live_data", "--basetemp", str(temporary / "pytest"), "--junitxml", str(result_dir / "junit.xml"), *args.tests]
    started_kst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat()
    started = time.monotonic()
    with (result_dir / "stdout.txt").open("w", encoding="utf-8") as output:
        try:
            result = subprocess.run(command, cwd=source, env=environment, stdout=output, stderr=subprocess.STDOUT, timeout=args.timeout)
            exit_code = result.returncode
        except subprocess.TimeoutExpired:
            exit_code = "TIMEOUT"
    manifest = json.loads((ROOT / "docs/refactoring/baseline_manifest.json").read_text(encoding="utf-8"))
    absent_baseline_paths = [p for p in manifest["files"] if not (source / p).is_file()]
    tested_sources = {p: hashlib.sha256((source / p).read_bytes()).hexdigest() for p in manifest["files"] if (source / p).is_file()}
    for directory in ("vibetrading", "tests", "scripts/refactoring"):
        for path in (source / directory).rglob("*.py"):
            if "__pycache__" not in path.parts:
                tested_sources[path.relative_to(source).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    evidence = {"started_kst": started_kst, "completed_kst": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(), "elapsed_seconds":time.monotonic()-started,"source":str(source),"command":command,"exit_code":exit_code,"environment_keys":sorted(environment),"guard_sha256":hashlib.sha256(guard.read_bytes()).hexdigest(),"launcher_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),"tested_source_hashes":tested_sources,"scope":"Offline Windows source-copy test; cooperative socket/subprocess guard, not an OS security sandbox, Linux durability or live-account verification"}
    evidence["absent_baseline_paths"] = absent_baseline_paths
    (result_dir / "result.json").write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in evidence.items() if k != "tested_source_hashes"},ensure_ascii=False))
    print((result_dir / "stdout.txt").read_text(encoding="utf-8")[-12000:])

if __name__ == "__main__":
    main()
