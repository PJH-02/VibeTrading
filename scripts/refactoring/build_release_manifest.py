"""Prepare a reviewable source/role inventory without building or deploying."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import subprocess
from pathlib import Path


PACKAGE = "vibetrading"
DOMAIN_PACKAGES = ("core", "feeds", "market", "storage", "strategies", "execution",
                   "portfolio", "runtime", "operations", "timefolio")


def canonical_source_hashes(root: Path) -> dict[str, str]:
    """Hash actual flat-domain sources; never substitute the old monolith."""
    sources = {}
    if (root / "src").is_symlink():
        raise ValueError("canonical source root symlink rejected")
    for name in DOMAIN_PACKAGES:
        package = root / "src" / name
        if package.is_symlink():
            raise ValueError("canonical package symlink rejected")
        if not package.is_dir():
            raise FileNotFoundError(f"canonical package missing: {name}")
        for path in sorted(package.rglob("*")):
            if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root):
                raise ValueError("canonical source symlink rejected")
            if not path.is_file() or path.suffix != ".py":
                continue
            sources[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return sources


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
    flat_sources = canonical_source_hashes(root) if (root / "src" / "runtime").is_dir() else {}
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
    for name, digest in flat_sources.items():
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


def build_local(root: Path, *, expected_head: str, paths) -> dict:
    """Stdlib-only readonly source review for local rollback preparation.

    Args:
        root: Repository root directory
        expected_head: Expected lowercase40hex git commit hash
        paths: List/tuple of 1-1024 unique canonical repository-relative file names

    Returns:
        Dict with review state including file hashes and verification status
    """
    # Validate parameters
    if not isinstance(root, Path):
        raise TypeError("root must be a Path object")
    if root.is_symlink():
        raise ValueError("root symlink rejected")
    if not root.is_dir():
        raise ValueError("root must be an existing directory")

    if not isinstance(expected_head, str):
        raise TypeError("expected_head must be a string")
    if len(expected_head) != 40 or not all(c in '0123456789abcdef' for c in expected_head):
        raise ValueError("expected_head must be lowercase40hex")

    if not isinstance(paths, (list, tuple)):
        raise TypeError("paths must be a list or tuple")
    if not (1 <= len(paths) <= 1024):
        raise ValueError("paths must contain 1-1024 entries")

    # Get actual git HEAD
    try:
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=root,
            capture_output=True,
            check=True
        )
        actual_head = result.stdout.decode('utf-8').strip().lower()
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise RuntimeError("git command failed or not available")

    if actual_head != expected_head:
        raise ValueError(f"HEAD mismatch: expected {expected_head}, got {actual_head}")

    # Validate paths and collect files
    files = {}
    allowed_suffixes = {'.py', '.json', '.yaml', '.yml', '.toml', '.md', '.txt'}
    forbidden_component_names = {'.', '..'}
    secret_components = {'.git', '.ssh', '.aws', 'secrets', 'runtime_data'}
    forbidden_suffixes = {'.key', '.pem', '.p12', '.database'}

    for path_str in paths:
        if not isinstance(path_str, str):
            raise TypeError("all paths must be strings")

        # Reject raw absolute paths
        if Path(path_str).is_absolute():
            raise ValueError(f"raw absolute path not allowed: {path_str}")

        # Canonicalization and validation of relative paths
        # Check for '.', '..', '//', and './' components and forbidden names
        normalized_path = Path(path_str).as_posix()
        if normalized_path != path_str: # Check for non-canonical forms like double slashes, etc.
             raise ValueError(f"non-canonical path: {path_str}")
        if "/./" in normalized_path or "/../" in normalized_path or normalized_path.startswith("./") or normalized_path.startswith("../"):
            raise ValueError(f"unsafe path components or aliases in: {path_str}")
        if normalized_path == "." or normalized_path == "..":
             raise ValueError(f"unsafe path components or aliases in: {path_str}")

        if path_str in files:
            raise ValueError("duplicate path rejected")
        parts = Path(path_str).parts
        if any(part in forbidden_component_names for part in parts):
            raise ValueError(f"unsafe path components in: {path_str}")

        if any(part in secret_components for part in parts):
             raise ValueError(f"secret path component in: {path_str}")

        # Check for '.env*' files
        if any(part.startswith(".env") for part in parts):
            raise ValueError(f"forbidden .env* file in: {path_str}")

        path = root / path_str

        # Check if resulting path is a symlink
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError("symlink not allowed (file or ancestor)")
        if path.is_symlink():
            raise ValueError(f"symlink not allowed: {path_str}")

        # Check file existence and type
        if not path.exists():
            raise ValueError(f"file not found: {path_str}")
        if not path.is_file():
            raise ValueError(f"not a regular file: {path_str}")

        # Check file extension and forbidden suffixes
        if path.suffix in forbidden_suffixes:
            raise ValueError(f"disallowed file extension/suffix: {path_str}")
        if path.suffix not in allowed_suffixes:
            raise ValueError(f"disallowed file extension: {path_str}")

        # Check if file is tracked in git
        try:
            result = subprocess.run(
                ['git', 'ls-files', '--error-unmatch', str(path)],
                cwd=root,
                capture_output=True,
                check=True
            )
            # No need to decode stdout here, just checking exit code
        except subprocess.CalledProcessError:
            raise ValueError(f"untracked file: {path_str}")

        # Verify file content matches committed blob
        try:
            result = subprocess.run(
                ['git', 'cat-file', 'blob', f'HEAD:{path_str}'],
                cwd=root,
                capture_output=True,
                check=True
            )
            committed_content = result.stdout
        except subprocess.CalledProcessError:
            raise ValueError(f"failed to read committed blob: {path_str}")

        actual_content = path.read_bytes()
        if actual_content != committed_content:
            raise ValueError(f"content drift: {path_str}")

        # Calculate SHA256
        sha256 = hashlib.sha256(actual_content).hexdigest()
        files[path_str] = sha256

    package_path = source_package(root)
    source_layout = "src" if package_path == root/"src"/PACKAGE else "flat"

    return {
        "schema_version": 1,
        "state": "LOCAL_REVIEW_PACKAGE_ONLY",
        "source_sha": expected_head,
        "source_layout": source_layout,
        "files": files,
        "server_deployed": False,
        "live_active": False,
        "build_image_verified": False,
        "external_gates": {
            "current_mount_device": "NOT_RUN",
            "immutable_candidate_image": "NOT_RUN",
            "rollback_image_digest": "NOT_RUN",
            "production_load": "NOT_RUN",
            "account_verification": "NOT_RUN"
        }
    }


if __name__=="__main__":
    main()
