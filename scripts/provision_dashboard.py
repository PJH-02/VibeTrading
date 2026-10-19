"""Run on the existing Linux server. Secrets arrive via stdin, never command arguments."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    configuration = json.loads(sys.stdin.readline())
    root = Path("/opt/vibetrading/dashboard")
    monitor = root / "monitor"
    monitor.mkdir(parents=True, exist_ok=True)
    os.chown(monitor, 10001, 10001)
    os.chmod(monitor, 0o700)
    source_image = subprocess.check_output(["docker", "inspect", "vibetrading-collectors-1", "--format", "{{.Image}}"], text=True).strip()
    image = "vibetrading-dashboard-base:20261004"
    subprocess.run(["docker", "tag", source_image, image], check=True)
    secret = Path("/etc/vibetrading/dashboard.env")
    secret.parent.mkdir(parents=True, exist_ok=True)
    values = {"DASHBOARD_SITE_URL": configuration["url"], "DASHBOARD_INGEST_TOKEN": configuration["token"],
              "DASHBOARD_SITE_BYPASS_TOKEN": configuration["bypass"], "DASHBOARD_APP_IMAGE": image}
    if any("\n" in value or "\r" in value for value in values.values()):
        raise ValueError("Invalid secret configuration")
    descriptor = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write("".join(key + "=" + value + "\n" for key, value in values.items()))
    # Generate the private key on the server. Existing registered accounts retain their key.
    code = """from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
import os
p=Path('/monitor/private.pem')
if not p.exists():
 key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
 p.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
 os.chmod(p,0o600)
"""
    subprocess.run(["docker", "run", "--rm", "--network", "none", "-v", str(monitor) + ":/monitor", image, "python", "-c", code], check=True)
    subprocess.run(["docker", "run", "--rm", "--user", "root", "--network", "none",
                    "-v", "/srv/vibetrading-data/testnet_latency_audits:/audits:ro",
                    "-v", str(monitor) + ":/exports",
                    "-v", str(root / "vibetrading/dashboard") + ":/app/vibetrading/dashboard:ro",
                    "-v", str(root / "scripts/export_dashboard_audits.py") + ":/app/export_audits.py:ro",
                    image, "python", "/app/export_audits.py"], check=True)
    subprocess.run(["docker", "build", "--build-arg", "DASHBOARD_APP_IMAGE=" + image,
                    "-t", "vibetrading-dashboard-timefolio:20261004", "-f", str(root / "deploy/Dockerfile.dashboard-timefolio"), str(root)], check=True)
    subprocess.run(["docker", "compose", "--env-file", str(secret), "-f", str(root / "deploy/dashboard-compose.yml"), "up", "-d"], check=True)
    print(json.dumps({"deployed": True, "services": ["bridge", "timefolio"]}))


if __name__ == "__main__":
    main()
