"""Prepare a reviewable source/role inventory without building or deploying."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path


PACKAGE = "vibetrading"


def source_package(root: Path) -> Path:
    flat, src = root/PACKAGE, root/"src"/PACKAGE
    if flat.is_symlink() or src.is_symlink() or (root/"src").is_symlink():
        raise ValueError("package source root symlink rejected")
    if flat.is_dir() and src.is_dir():
        raise ValueError("ambiguous package source roots: vibetrading and src/vibetrading both exist")
    if src.is_dir():
        return src
    if flat.is_dir():
        return flat
    raise FileNotFoundError("package source root missing")


def build(root: Path, snapshot: dict) -> dict:
    baseline = json.loads((root/"docs/refactoring/baseline_manifest.json").read_text(encoding="utf-8"))
    before = baseline["files"]
    package = source_package(root)
    sources = {}
    changed = {}
    for path in sorted(package.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        name = (Path(PACKAGE)/path.relative_to(package)).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        sources[name] = digest
        old = before.get(name)
        if isinstance(old, dict):
            old = old.get("sha256")
        if old != digest:
            changed[name] = {"before_sha256":old,"after_sha256":digest}
    roles = []
    for container in snapshot["containers"]:
        remote = container.get("source_hashes", {})
        checks = {}
        for name, diff in changed.items():
            observed = remote.get(name)
            checks[name] = ("NEW_MODULE" if diff["before_sha256"] is None else
                "BASELINE_MATCH" if observed == diff["before_sha256"] else
                "UNOBSERVED" if observed is None else "SOURCE_DRIFT_REVIEW_REQUIRED")
        roles.append({"container":container["name"],"current_image":container["image"],
            "rollback_image_id":container["image_id"],"command":container["command"],
            "compose_files":container["compose_files"],"mounts":container["mounts"],
            "memory_limit_bytes":container["memory_limit"],"source_compatibility":checks,
            "candidate_image_digest":None,"deployment_status":"NOT_BUILT_NOT_DEPLOYED"})
    total_limits = sum(r["memory_limit_bytes"] for r in roles)
    lines = snapshot["memory"]["stdout"].splitlines()
    ram = next(int(line.split()[1]) for line in lines if line.strip().startswith("Mem:"))
    blockers = ["72-hour representative capacity and source ingress trace not verified",
        "Candidate Linux durability and role-specific immutable images not built/verified",
        "Actual account reconciliation and noncooperating legacy owner fencing not verified",
        "Production risk/TTL/feed lease and adapter mutation permit semantics unverified",
        "Deployment mount contracts must be collected from current host/container identities"]
    if total_limits > ram:
        blockers.append("Current concurrent container memory limits exceed physical RAM")
    if any("SOURCE_DRIFT_REVIEW_REQUIRED" in r["source_compatibility"].values() for r in roles):
        blockers.append("Installed role source differs from local refactoring preimage")
    return {"schema_version":1,"generated_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "state":"REVIEW_PACKAGE_DEPLOYMENT_BLOCKED","snapshot_observed_utc":snapshot["observed_utc"],
        "baseline_source_set_sha256":baseline["source_set_sha256"],"source_hashes":sources,
        "changed_sources":changed,"roles":roles,
        "resources":{"cpu_count":snapshot["cpu_count"],"physical_ram_bytes":ram,
            "current_container_limit_sum_bytes":total_limits,"candidate_limits":"UNVERIFIED"},
        "blockers":blockers,
        "prohibitions":["No wholesale application of older local Compose to production",
            "No restore of an old trading DB over current fills",
            "No stream/consumer reset or raw/remote object deletion",
            "No activation of retired feeds, real accounts or inactive strategies"]}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--snapshot",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    output=args.output.resolve()
    if not output.is_relative_to(root/"docs/refactoring"):
        raise ValueError("release review output must stay under docs/refactoring")
    result=build(root,json.loads(args.snapshot.read_text(encoding="utf-8")))
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"state":result["state"],"changed_sources":len(result["changed_sources"]),
        "roles":len(result["roles"]),"blockers":len(result["blockers"])},ensure_ascii=False))


if __name__=="__main__":
    main()
