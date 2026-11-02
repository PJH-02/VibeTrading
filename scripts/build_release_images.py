"""Freeze and verify candidates, or explicitly assemble four offline images."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.refactoring.release_candidate import (EXPECTED_IMAGE_CMD, EXPECTED_IMAGE_USER, canonical_digest,
    freeze, load_json, verify, verify_wheel, write_manifest, _validate_images)

MANIFEST = ROOT / ".deploy-release.json"
DOCKERFILES = {"app": "Dockerfile", "crypto": "Dockerfile.crypto",
               "ledger": "deploy/Dockerfile.crypto-ledger", "browser": "deploy/Dockerfile.dashboard-timefolio"}
BASE_ENV = {role: (f"VIBETRADING_{role.upper()}_BASE_REF", f"VIBETRADING_{role.upper()}_BASE_ID")
            for role in ("app", "crypto")}


def _inspect(runner, image: str) -> dict | None:
    result = runner(["docker", "image", "inspect", image], check=False, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if result.returncode:
        return None
    return json.loads(result.stdout)[0]


def _cached_base_ref(runner, image_id: str) -> str:
    """Resolve a local-only build reference and retain its exact daemon ID."""
    reference = "vibetrading-release-cache:sha256-" + image_id.removeprefix("sha256:")
    existing = _inspect(runner, reference)
    if existing is None:
        runner(["docker", "tag", image_id, reference], check=True)
        existing = _inspect(runner, reference)
    if existing is None or existing.get("Id") != image_id:
        raise ValueError("immutable image tag collision: cached base")
    return reference


def _build_images(candidate: dict, context_root: Path, *, runner=subprocess.run, environ=os.environ) -> dict:
    """Build all four classes with pinned cached bases and validate OCI labels."""
    receipts = {}
    source_sha, config_sha, input_sha = (candidate[k] for k in ("source_sha256", "config_sha256", "input_sha256"))
    revision = candidate["source_head"]
    with tempfile.TemporaryDirectory(prefix="vibetrading-release-") as temporary:
        context = Path(temporary)
        for rel in candidate["files"]:
            src = context_root / rel
            dst = context / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        base_refs = {}
        base_ids = {}
        for role, dockerfile in DOCKERFILES.items():
            if role in BASE_ENV:
                ref_var, id_var = BASE_ENV[role]
                base_ref, base_id = environ.get(ref_var, ""), environ.get(id_var, "")
                if not base_ref or not base_id.startswith("sha256:") or len(base_id) != 71:
                    raise ValueError(f"{ref_var} and full {id_var} are required")
                cached = _inspect(runner, base_ref)
                if not cached or cached.get("Id") != base_id:
                    raise ValueError(f"cached base identity mismatch: {role}")
                pinned = _inspect(runner, base_id)
                if not pinned or pinned.get("Id") != base_id:
                    raise ValueError(f"pinned cached base ID unavailable: {role}")
            else:
                dependency_role = "crypto" if role == "ledger" else "app"
                dependency = receipts[dependency_role]
                base_id = dependency["id"]
                base_ref = base_id
                cached = _inspect(runner, base_ref)
                if not cached or cached.get("Id") != base_id:
                    raise ValueError(f"dependent base image identity mismatch: {role}")
            base_ref = _cached_base_ref(runner, base_id)
            base_refs[role], base_ids[role] = base_ref, base_id
            tag = f"vibetrading-{role}:rel01-{input_sha[:20]}"
            labels = {"org.opencontainers.image.revision": revision,
                      "org.vibetrading.config-hash": config_sha,
                      "org.vibetrading.source-sha256": source_sha,
                      "org.vibetrading.config-sha256": config_sha,
                      "org.vibetrading.input-sha256": input_sha,
                      "org.vibetrading.base-id": base_id,
                      "org.vibetrading.image-class": role}
            existing = _inspect(runner, tag)
            if existing:
                got = existing.get("Config", {}).get("Labels") or {}
                if any(got.get(k) != v for k, v in labels.items()):
                    raise ValueError(f"immutable image tag collision: {role}")
                identity = existing
            else:
                cmd = ["docker", "build", "--pull=false", "--network=none", "--file", dockerfile,
                       "--tag", tag, "--build-arg", f"VIBETRADING_BASE_IMAGE={base_ref}",
                       "--build-arg", f"DASHBOARD_APP_IMAGE={base_ref}",
                       "--build-arg", "VIBETRADING_REUSE_APP_BASE=1",
                       "--build-arg", "REUSE_CACHED_BROWSER=1",
                       "--build-arg", f"VIBETRADING_BUILD_REVISION={revision}",
                       "--build-arg", f"VIBETRADING_CONFIG_HASH={config_sha}",
                       "--build-arg", f"VIBETRADING_SOURCE_SHA256={source_sha}",
                       "--build-arg", f"VIBETRADING_CONFIG_SHA256={config_sha}",
                       "--build-arg", f"VIBETRADING_INPUT_SHA256={input_sha}",
                       "--build-arg", f"VIBETRADING_BASE_ID={base_ids[role]}",
                       "--build-arg", f"VIBETRADING_IMAGE_CLASS={role}", "."]
                runner(cmd, cwd=context, check=True)
                identity = _inspect(runner, tag)
                if not identity:
                    raise ValueError(f"built image unavailable after build: {role}")
                got = identity.get("Config", {}).get("Labels") or {}
                if any(got.get(k) != v for k, v in labels.items()):
                    raise ValueError(f"built image label/source/config mismatch: {role}")
            pinned_after = _inspect(runner, base_ref)
            if not pinned_after or pinned_after.get("Id") != base_id:
                raise ValueError("cached base identity changed during build")
            config = identity.get("Config", {})
            if (config.get("Cmd") != EXPECTED_IMAGE_CMD[role]
                    or config.get("User") != EXPECTED_IMAGE_USER[role]
                    or "PYTHONPATH=/app/src" not in config.get("Env", [])):
                raise ValueError(f"image runtime identity incomplete: {role}")
            if f"VIBETRADING_BUILD_CONFIG_HASH={config_sha}" not in config.get("Env", []):
                raise ValueError(f"image config environment mismatch: {role}")
            browser_path = None
            if role == "browser":
                browser_path = next((v.split("=", 1)[1] for v in config.get("Env", []) if v.startswith("PLAYWRIGHT_BROWSERS_PATH=")), None)
                if browser_path != "/opt/dashboard-browsers":
                    raise ValueError("browser image path mismatch")
            deps = runner(["docker", "run", "--rm", "--network=none", "--read-only", "--cap-drop=ALL",
                           "--security-opt=no-new-privileges", "--user", "10001:10001", "--pids-limit=64",
                           "--memory=256m", "--cpus=1", "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
                           "--entrypoint", "python", tag,
                           "-c", "import importlib.metadata as m,json; print(json.dumps({d.metadata['Name']:d.version for d in m.distributions() if d.metadata.get('Name')}))"],
                          check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
            dependency_versions = json.loads(deps)
            receipts[role] = {"id": identity["Id"], "source_revision": revision,
                "tag": tag,
                "source_sha256": source_sha, "config_sha256": config_sha, "input_sha256": input_sha,
                "base_id": base_ids[role], "command": config["Cmd"], "user": config["User"],
                "pythonpath": "/app/src", "dependency_versions": dependency_versions,
                **({"browser_path": browser_path} if browser_path else {})}
    return receipts


def _json_arg(path: Path | None):
    if path is None:
        return None
    return load_json(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("plan", "freeze", "verify", "build"):
        p = sub.add_parser(action)
        p.add_argument("--manifest", type=Path, default=MANIFEST)
        if action in ("verify", "build"):
            p.add_argument("--expected-manifest-sha256")
        if action in ("freeze", "verify", "build"):
            p.add_argument("--images", type=Path)
            p.add_argument("--server-snapshot", type=Path)
            p.add_argument("--wheel", type=Path, help="wheel archive; verify_wheel checks RECORD and source")
            p.add_argument("--wheel-sha256")
    args = parser.parse_args(argv)
    try:
        if args.action in ("plan", "freeze"):
            images = _json_arg(getattr(args, "images", None))
            wheel = None
            wheel_path = getattr(args, "wheel", None)
            if wheel_path:
                if not args.wheel_sha256:
                    raise ValueError("--wheel requires --wheel-sha256")
                wheel = {"path": str(wheel_path), "expected_sha256": args.wheel_sha256}
            snapshot_path = getattr(args, "server_snapshot", None)
            server_snapshot = load_json(snapshot_path) if snapshot_path else None
            candidate = freeze(ROOT, images=images, wheel=wheel, server_snapshot=server_snapshot)
            print(json.dumps({"source_head": candidate["source_head"], "files": len(candidate["files"]),
                "state": candidate["deployment_state"], "manifest_sha256": candidate["manifest_sha256"]}, sort_keys=True))
            if args.action == "freeze":
                write_manifest(args.manifest, candidate)
            return 0
        candidate = load_json(args.manifest)
        images = _json_arg(getattr(args, "images", None))
        wheel_path = getattr(args, "wheel", None)
        wheel = {"path": str(wheel_path), "expected_sha256": getattr(args, "wheel_sha256", None)} if wheel_path else None
        snapshot_path = getattr(args, "server_snapshot", None)
        server_snapshot = load_json(snapshot_path) if snapshot_path else None
        if candidate.get("wheel") and not wheel_path:
            raise ValueError("--wheel is required to verify the frozen wheel archive")
        verify(ROOT, candidate, images=images, wheel=wheel, server_snapshot=server_snapshot,
               expected_manifest_sha256=getattr(args, "expected_manifest_sha256", None))
        if args.action == "verify":
            print(json.dumps({"verified": True, "state": candidate["deployment_state"], "manifest_sha256": candidate["manifest_sha256"]}, sort_keys=True))
            return 0
        if not wheel_path and candidate.get("wheel"):
            raise ValueError("--wheel is required before image build")
        built_images = _build_images(candidate, ROOT)
        candidate = freeze(ROOT, expected_head=candidate["source_head"], images=built_images,
                           wheel=wheel, server_snapshot=server_snapshot)
        write_manifest(args.manifest, candidate)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
        detail = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "bounded operation failed"
        print(f"release {args.action} failed ({type(exc).__name__}): {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
