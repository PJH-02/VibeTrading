"""Copy the current authorized source set without environment files or datasets."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

RUN = Path(__file__).resolve().parent
ROOT = RUN.parents[3]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    args = parser.parse_args()
    if not args.label.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid candidate label")
    destination = ROOT / "tmp/refactoring/20261018-stage-assignment" / args.label
    destination.mkdir(parents=True, exist_ok=False)
    baseline = json.loads((ROOT / "docs/refactoring/baseline_manifest.json").read_text(encoding="utf-8"))
    selected = {ROOT / p for p in baseline["files"]}
    for folder in ("vibetrading", "tests/refactoring", "scripts/refactoring"):
        selected.update(p for p in (ROOT / folder).rglob("*.py") if "__pycache__" not in p.parts)
    selected.add(RUN / "schema_oracle.json")
    selected.add(RUN / "encoding_oracle.json")
    selected.add(ROOT / "tests/test_local_snapshot.py")
    hashes = {}
    for source in sorted(selected):
        if source.is_symlink() or not source.is_file():
            raise RuntimeError("Unexpected selected source: " + str(source))
        if source.name.startswith(".env") or source.suffix in {".db", ".sqlite3", ".pem", ".key", ".pyc"}:
            raise RuntimeError("Forbidden source: " + str(source))
        relative = source.relative_to(ROOT)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        hashes[relative.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    output = {"source": str(destination), "source_hashes": hashes}
    (RUN / (args.label + "-source.json")).write_text(json.dumps(output, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"source": str(destination), "files": len(hashes)}, ensure_ascii=False))

if __name__ == "__main__":
    main()
