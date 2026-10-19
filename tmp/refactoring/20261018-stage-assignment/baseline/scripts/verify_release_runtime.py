from __future__ import annotations

"""Verify running Compose containers match the immutable Release 1 manifest."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CRYPTO_SERVICES = {"crypto-storage", "crypto-maintenance", "crypto-collector-binance",
                   "crypto-collector-bitget", "crypto-collector-bybit"}
APP_SERVICES = {"collectors", "storage", "maintenance"}
DEFAULT_SERVICES = ("collectors", "storage", "maintenance", "crypto-storage",
                    "crypto-maintenance", "crypto-collector-binance",
                    "crypto-collector-bitget", "crypto-collector-bybit")


def _run(command: list[str]) -> str:
    result = subprocess.run(command, cwd=ROOT, check=True, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default=".deploy-release.json")
    parser.add_argument("--compose-env", action="append")
    parser.add_argument("--base-compose", default="docker-compose.yml")
    parser.add_argument("--crypto-profile", default="deploy/crypto-profile.yml")
    parser.add_argument("--release-overlay", default="deploy/release1-overlay.yml")
    parser.add_argument("--service", action="append", dest="services")
    args = parser.parse_args()
    manifest = json.loads((ROOT / args.manifest).read_text(encoding="utf-8"))
    command = ["docker", "compose"]
    for env_file in args.compose_env or ["deploy/release.env"]:
        command.extend(["--env-file", env_file])
    command.extend(["-f", args.base_compose, "-f", args.crypto_profile,
                    "-f", args.release_overlay, "ps", "-q", "--all"])
    failures: list[str] = []
    checked = 0
    services = args.services or list(DEFAULT_SERVICES)
    for service in services:
        try:
            container_ids = _run(command + [service]).splitlines()
        except subprocess.CalledProcessError as exc:
            failures.append(f"{service}: compose inspect failed ({exc.returncode})")
            continue
        if not container_ids:
            failures.append(f"{service}: no container found")
            continue
        try:
            containers = json.loads(_run(["docker", "inspect", *container_ids]))
        except (json.JSONDecodeError, subprocess.CalledProcessError) as exc:
            failures.append(f"{service}: container inspect failed ({type(exc).__name__})")
            continue
        for container in containers:
            try:
                actual_service = (container.get("Config", {}).get("Labels") or {}).get("com.docker.compose.service", "")
                if actual_service != service:
                    failures.append(f"{service}: got unexpected service {actual_service}")
                    continue
                crypto = service in CRYPTO_SERVICES
                if service not in APP_SERVICES and not crypto:
                    continue
                expected = manifest["images"]["crypto" if crypto else "app"]
                actual_id = str(container.get("Image", ""))
                if actual_id != expected["id"]:
                    failures.append(f"{service}: image id {actual_id} != {expected['id']}")
                    continue
                env = dict(item.split("=", 1) for item in container.get("Config", {}).get("Env", []) if "=" in item)
                expected_env = {
                    "VIBETRADING_IMAGE_ID": expected["id"],
                    "VIBETRADING_BUILD_REVISION": manifest["build_revision"],
                    "VIBETRADING_BUILD_CONFIG_HASH": manifest["config_hash"],
                }
                mismatches = [key for key, value in expected_env.items() if env.get(key) != value]
                if mismatches:
                    failures.append(f"{service}: runtime provenance mismatch ({', '.join(mismatches)})")
                    continue
                image = json.loads(_run(["docker", "image", "inspect", actual_id]))[0]
                labels = image.get("Config", {}).get("Labels") or {}
                if (labels.get("org.opencontainers.image.revision") != manifest["build_revision"]
                        or labels.get("org.vibetrading.config-hash") != manifest["config_hash"]):
                    failures.append(f"{service}: image labels differ from release manifest")
                    continue
                checked += 1
            except (KeyError, IndexError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
                failures.append(f"{service}: verification error ({type(exc).__name__})")
    if failures:
        print("Release verification failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    if checked == 0:
        print("Release verification failed: no matching containers found")
        return 1
    print(f"Release {manifest['build_revision']} verified on {checked} containers; "
          f"config={manifest['config_hash']}; images="
          f"app:{manifest['images']['app']['id']} crypto:{manifest['images']['crypto']['id']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"release verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
