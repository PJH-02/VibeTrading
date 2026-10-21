"""Pin current allowlisted source into a credential-free, never-overwritten copy."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    parser.add_argument("--exclude", action="append", default=[], help="Explicit unfinished source path to exclude")
    args = parser.parse_args()
    if not args.label.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid source label")
    run = Path(__file__).resolve().parent
    root = run.parents[3]
    manifest = json.loads((root / "docs/refactoring/vnext/source_manifest.json").read_text(encoding="utf-8"))
    removed = {name for name in manifest["files"] if not (root / name).is_file()}
    selected = set(manifest["files"]) - removed
    for directory in ("vibetrading", "tests", "scripts/refactoring"):
        selected.update(path.relative_to(root).as_posix()
                        for path in (root / directory).rglob("*.py")
                        if "__pycache__" not in path.parts)
    excluded = set(args.exclude)
    if not excluded <= selected:
        raise ValueError("Unknown excluded source path")
    selected -= excluded
    destination = root / "tmp/refactoring/20261020-production" / args.label
    destination.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name in sorted(selected):
        source = root / name
        if (source.is_symlink() or not source.is_file()
                or not source.resolve().is_relative_to(root)
                or source.name.startswith(".env")
                or source.suffix in {".db", ".sqlite3", ".pem", ".key", ".pyc"}):
            raise ValueError("Unsafe source path: " + name)
        raw = source.read_bytes()
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    source_set = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    proof = {"source": str(destination), "git_head": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root).decode().strip(),
        "source_set_sha256": source_set, "files": hashes,
        "excluded_work_in_progress": sorted(excluded),
        "removed_baseline_paths": sorted(removed),
        "scope": "Current local candidate; credential-free copy, not a test PASS"}
    (run / (args.label + "-source.json")).write_text(
        json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"source": str(destination), "files": len(hashes), "source_set_sha256": source_set}))


if __name__ == "__main__":
    main()
