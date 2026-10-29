#!/usr/bin/env python3
"""Credential-free shape checks for WEB-01 configuration; never contacts hosts."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import re
import sys
from pathlib import Path
from urllib.parse import urlparse


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def errors_for(env: dict[str, str], local_fixture: bool = False) -> list[str]:
    errors: list[str] = []
    origin = env.get("VIEWER_ORIGIN", "")
    parsed = urlparse(origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        errors.append("VIEWER_ORIGIN must be an exact HTTPS origin")
    if not re.fullmatch(r"[0-9a-f]{24}", env.get("VIEWER_OWNER_SCOPE", "")):
        errors.append("VIEWER_OWNER_SCOPE must be one 24-hex owner scope")
    if not local_fixture and parsed.hostname:
        host = parsed.hostname.lower().rstrip(".")
        try:
            address = ipaddress.ip_address(host)
            if not address.is_global:
                errors.append("production VIEWER_ORIGIN must not use loopback/private/reserved IP")
        except ValueError:
            if host == "localhost" or host.endswith((".localhost", ".invalid", ".test", ".example")) or host in {"example.com", "example.net", "example.org"}:
                errors.append("production VIEWER_ORIGIN cannot use localhost or an example-only .invalid host")
    owner_proof = env.get("D1_OWNER_SCOPE_PROOF_REF", "")
    if not ((local_fixture and owner_proof.startswith("LOCAL_FIXTURE")) or (not local_fixture and owner_proof.startswith("APPROVED_CUT01:"))):
        errors.append("D1_OWNER_SCOPE_PROOF_REF must confirm this D1 dataset contains only the configured owner")
    for name in ("VIEWER_TOKEN_SHA256", "PRIVATE_SERVICE_TOKEN_SHA256"):
        value = env.get(name, "")
        if not re.fullmatch(r"[0-9a-f]{64}", value, re.I):
            errors.append(f"{name} must be a SHA-256 hex digest")
    if env.get("VIEWER_TOKEN_SHA256") and env.get("VIEWER_TOKEN_SHA256", "").lower() == env.get("PRIVATE_SERVICE_TOKEN_SHA256", "").lower():
        errors.append("viewer and private-service credentials must be independent")
    secret = env.get("VIEWER_SESSION_SECRET", "")
    if len(secret) < 32 or secret.lower().startswith("replace_"):
        errors.append("VIEWER_SESSION_SECRET must be an independent secret of at least 32 characters")
    elif hashlib.sha256(secret.encode()).hexdigest() in {env.get("VIEWER_TOKEN_SHA256", "").lower(), env.get("PRIVATE_SERVICE_TOKEN_SHA256", "").lower()}:
        errors.append("VIEWER_SESSION_SECRET must be separate from viewer and private-service credentials")
    for name in ("PRIVATE_GATEWAY_BINDING", "PRIVATE_GATEWAY_PROOF_REF"):
        value = env.get(name, "")
        if not value or value.startswith("REQUIRED_"):
            errors.append(f"{name} must reference the configured private gateway and its approved proof")
    binding = env.get("PRIVATE_GATEWAY_BINDING", "")
    if binding and not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", binding):
        errors.append("PRIVATE_GATEWAY_BINDING must be a named Cloudflare service binding")
    proof = env.get("PRIVATE_GATEWAY_PROOF_REF", "")
    if proof and not ((local_fixture and proof.startswith("LOCAL_FIXTURE")) or (not local_fixture and proof.startswith("APPROVED_CUT01:"))):
        errors.append("private gateway proof must be a local fixture or approved CUT-01 evidence reference")
    tls_mode = env.get("TLS_MODE", "")
    expected_tls_mode = "LOCAL_FIXTURE" if local_fixture else "managed-existing-ingress"
    if tls_mode != expected_tls_mode:
        errors.append(f"TLS_MODE must be {expected_tls_mode}")
    for name in ("DNS_PROOF_REF", "TLS_PROOF_REF", "CERT_RENEWAL_PROOF_REF"):
        value = env.get(name, "")
        required_prefix = "LOCAL_FIXTURE" if local_fixture else "APPROVED_CUT01:"
        if not value.startswith(required_prefix):
            errors.append(f"{name} must reference verified DNS/TLS/certificate-renewal evidence")
    bridge = env.get("DASHBOARD_SITE_URL", "")
    bridge_url = urlparse(bridge)
    if bridge_url.scheme != "https" or not bridge_url.hostname or bridge_url.username or bridge_url.password or bridge_url.port not in (None, 443) or bridge_url.path not in ("", "/") or bridge_url.query or bridge_url.fragment:
        errors.append("DASHBOARD_SITE_URL must be a credential-free HTTPS private gateway origin")
    elif parsed.hostname and bridge_url.hostname.lower() == parsed.hostname.lower():
        errors.append("bridge ingest URL must not be the public viewer hostname")
    elif not local_fixture and (bridge_url.hostname.lower() == "localhost" or bridge_url.hostname.lower().endswith((".localhost", ".invalid", ".test", ".example")) or bridge_url.hostname.lower() in {"example.com", "example.net", "example.org"}):
        errors.append("production DASHBOARD_SITE_URL cannot use a loopback or example-only host")
    backend = env.get("PUBLIC_BACKEND_BIND", "")
    if not backend:
        errors.append("PUBLIC_BACKEND_BIND must explicitly state none or a loopback-only bind")
    if backend and ("0.0.0.0" in backend or "::" in backend):
        errors.append("public frontend/backend listener must not bind an unrestricted host interface")
    if backend and backend.lower() != "none" and not backend.startswith(("127.0.0.1:", "localhost:")):
        errors.append("PUBLIC_BACKEND_BIND must be none or an explicit loopback-only address")
    ports = env.get("PUBLIC_EXPOSED_PORTS", "").strip()
    if not ports:
        errors.append("PUBLIC_EXPOSED_PORTS must explicitly state none")
    elif ports.lower() != "none":
        errors.append("no direct dashboard backend port may be publicly exposed")
    mounts = env.get("CREDENTIAL_MOUNTS", "").strip()
    if not mounts:
        errors.append("CREDENTIAL_MOUNTS must explicitly state none")
    elif mounts.lower() != "none":
        errors.append("dashboard frontend must not mount broker or account credential files")
    if not local_fixture and not proof:
        errors.append("production requires an approved private-gateway connectivity proof reference")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True, type=Path)
    parser.add_argument("--local-fixture", action="store_true", help="accept explicit local-only proof fixture")
    args = parser.parse_args()
    errors = errors_for(read_env(args.env), args.local_fixture)
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("PASS: configuration shape only; TLS, binding reachability, and deployment remain unverified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
