from __future__ import annotations

"""Build content-addressed Release 1 images and emit a verifiable manifest."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IGNORED_PARTS = {
    ".git", ".server_patch", ".deploy-backups", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".analysis-venv", ".codex_remote", ".matplotlib-cache",
    "__pycache__", "runtime_data", "data", "exports", "docs", "tests",
}
IGNORED_SUFFIXES = {".pyc", ".parquet", ".sqlite3", ".log"}
IGNORED_NAMES = {".env", ".deploy-release.json"}


def _included(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    if any(part in IGNORED_PARTS or part.startswith(".server_patch") for part in relative.parts):
        return False
    if path.name in IGNORED_NAMES or path.suffix in IGNORED_SUFFIXES:
        return False
    if path.name.startswith(".env.") and path.name != ".env.example":
        return False
    if path.name.endswith("release.env"):
        return False
    return path.is_file()


def _digest(paths: list[Path]) -> str:
    hasher = hashlib.sha256()
    for path in sorted(set(paths), key=lambda item: item.relative_to(ROOT).as_posix()):
        relative = path.relative_to(ROOT).as_posix().encode()
        content = path.read_bytes()
        hasher.update(len(relative).to_bytes(4, "big"))
        hasher.update(relative)
        hasher.update(len(content).to_bytes(8, "big"))
        hasher.update(content)
    return hasher.hexdigest()


def _files() -> list[Path]:
    output: list[Path] = []
    for directory, child_dirs, filenames in os.walk(ROOT):
        child_dirs[:] = sorted(name for name in child_dirs
                                if name not in IGNORED_PARTS and not name.startswith(".server_patch"))
        parent = Path(directory)
        for filename in filenames:
            path = parent / filename
            if _included(path):
                output.append(path)
    return output


def _run(command: list[str]) -> str:
    result = subprocess.run(command, cwd=ROOT, check=True, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.stdout:
        print(result.stdout.rstrip())
    return result.stdout.strip()


def _inspect(image: str) -> tuple[str, dict[str, str]] | None:
    result = subprocess.run(
        ["docker", "image", "inspect", image], cwd=ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    if result.returncode:
        return None
    image = json.loads(result.stdout)[0]
    return str(image["Id"]), image.get("Config", {}).get("Labels") or {}


def _image_id(image: str) -> str:
    inspected = _inspect(image)
    if inspected is None:
        _run(["docker", "pull", image])
        inspected = _inspect(image)
    if inspected is None:
        raise RuntimeError(f"base image is unavailable: {image}")
    return inspected[0]


def _build(image: str, dockerfile: str, revision: str, config_hash: str,
           base_image: str, *, reuse_app_base: bool = False) -> str:
    existing = _inspect(image)
    if existing:
        image_id, labels = existing
        expected = (revision, config_hash)
        actual = (labels.get("org.opencontainers.image.revision"),
                  labels.get("org.vibetrading.config-hash"))
        if actual != expected:
            raise RuntimeError(f"immutable tag collision for {image}: labels={actual}")
        return image_id
    command = ["docker", "build", "--file", dockerfile, "--tag", image,
               "--build-arg", f"VIBETRADING_BUILD_REVISION={revision}",
               "--build-arg", f"VIBETRADING_CONFIG_HASH={config_hash}"]
    command.extend(["--build-arg", f"VIBETRADING_BASE_IMAGE={base_image}"])
    if dockerfile == "Dockerfile":
        command.extend(["--build-arg", f"VIBETRADING_REUSE_APP_BASE={'1' if reuse_app_base else '0'}"])
    command.append(".")
    _run(command)
    inspected = _inspect(image)
    if not inspected:
        raise RuntimeError(f"docker built {image}, but image inspect failed")
    image_id, labels = inspected
    actual = (labels.get("org.opencontainers.image.revision"),
              labels.get("org.vibetrading.config-hash"))
    if actual != (revision, config_hash):
        raise RuntimeError(f"image metadata mismatch for {image}: {actual}")
    return image_id


def _write_env(path: Path, values: dict[str, str]) -> None:
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")


def main() -> int:
    files = _files()
    source_hash = _digest(files)
    config_paths = [path for path in files if (
        path.is_relative_to(ROOT / "config")
        or path.name in {"docker-compose.yml", "nats.conf"}
        or (path.parent == ROOT / "deploy" and path.suffix in {".yaml", ".yml"})
    )]
    config_hash = _digest(config_paths)
    app_base = os.getenv("VIBETRADING_RELEASE_APP_BASE", "python:3.12-slim")
    app_base_id = _image_id(app_base)
    crypto_base = os.getenv("VIBETRADING_RELEASE_CRYPTO_BASE", "")
    if crypto_base:
        crypto_base_id = _image_id(crypto_base)
    else:
        crypto_base = ""
        crypto_base_id = ""
    release_seed = hashlib.sha256(f"{source_hash}:{app_base_id}:{crypto_base_id}".encode()).hexdigest()
    revision = f"r1-{release_seed[:20]}"
    app_image = f"vibetrading-app:{revision}"
    app_id = _build(app_image, "Dockerfile", revision, config_hash, app_base,
                    reuse_app_base=app_base.startswith("vibetrading-app:"))
    if not crypto_base:
        crypto_base = app_image
        crypto_base_id = app_id
    crypto_hash = hashlib.sha256(f"{source_hash}:{app_id}:{crypto_base_id}:exchange-calendars==4.13.2".encode()).hexdigest()
    crypto_image = f"vibetrading-crypto:r1-{crypto_hash[:20]}"
    crypto_id = _build(crypto_image, "Dockerfile.crypto", revision, config_hash, crypto_base)

    app_env = ROOT / "deploy" / "app-release.env"
    crypto_env = ROOT / "deploy" / "crypto-release.env"
    release_env = ROOT / "deploy" / "release.env"
    _write_env(app_env, {
        "VIBETRADING_BUILD_REVISION": revision,
        "VIBETRADING_BUILD_CONFIG_HASH": config_hash,
        "VIBETRADING_IMAGE_ID": app_id,
    })
    _write_env(crypto_env, {
        "VIBETRADING_BUILD_REVISION": revision,
        "VIBETRADING_BUILD_CONFIG_HASH": config_hash,
        "VIBETRADING_IMAGE_ID": crypto_id,
    })
    _write_env(release_env, {
        "VIBETRADING_APP_IMAGE": app_image,
        "VIBETRADING_CRYPTO_IMAGE": crypto_image,
        "VIBETRADING_APP_RELEASE_ENV_FILE": app_env.resolve().as_posix(),
        "VIBETRADING_CRYPTO_RELEASE_ENV_FILE": crypto_env.resolve().as_posix(),
        "VIBETRADING_RELEASE_DIR": ROOT.resolve().as_posix(),
    })
    manifest = {
        "release": "1",
        "build_revision": revision,
        "config_hash": config_hash,
        "source_hash": source_hash,
        "source_hashes": {
            path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files if path.relative_to(ROOT).parts[0] == "src"
        },
        "primary_compose": "deploy/primary-compose.yml",
        "base_images": {
            "app": {"tag": app_base, "id": app_base_id},
            "crypto": {"tag": crypto_base, "id": crypto_base_id},
        },
        "images": {
            "app": {"tag": app_image, "id": app_id},
            "crypto": {"tag": crypto_image, "id": crypto_id},
        },
    }
    (ROOT / ".deploy-release.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    print("Compose files can now be run with --env-file deploy/release.env.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"release image build failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
