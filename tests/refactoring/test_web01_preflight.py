import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "dev" / "web_access_preflight.py"
SPEC = importlib.util.spec_from_file_location("web_access_preflight", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class WebAccessPreflightTests(unittest.TestCase):
    def valid_env(self):
        return {
            "VIEWER_ORIGIN": "https://viewer.example.test",
            "VIEWER_TOKEN_SHA256": "a" * 64,
            "VIEWER_SESSION_SECRET": "viewer-session-secret-0123456789abcdef",
            "VIEWER_OWNER_SCOPE": "0123456789abcdef01234567",
            "D1_OWNER_SCOPE_PROOF_REF": "LOCAL_FIXTURE_D1_OWNER",
            "PRIVATE_SERVICE_TOKEN_SHA256": "b" * 64,
            "PRIVATE_GATEWAY_BINDING": "PRIVATE_DASHBOARD_SERVICE",
            "PRIVATE_GATEWAY_PROOF_REF": "LOCAL_FIXTURE_01",
            "TLS_MODE": "LOCAL_FIXTURE",
            "DNS_PROOF_REF": "LOCAL_FIXTURE_DNS",
            "TLS_PROOF_REF": "LOCAL_FIXTURE_TLS",
            "CERT_RENEWAL_PROOF_REF": "LOCAL_FIXTURE_RENEWAL",
            "DASHBOARD_SITE_URL": "https://private-gateway.example.test",
            "PUBLIC_BACKEND_BIND": "none",
            "PUBLIC_EXPOSED_PORTS": "none",
            "CREDENTIAL_MOUNTS": "none",
        }

    def test_valid_fixture_passes_shape_only(self):
        self.assertEqual(MODULE.errors_for(self.valid_env(), local_fixture=True), [])

    def test_missing_credentials_and_private_proof_fail_closed(self):
        errors = MODULE.errors_for({"VIEWER_ORIGIN": "http://viewer.test"}, local_fixture=True)
        self.assertTrue(any("HTTPS origin" in error for error in errors))
        self.assertTrue(any("VIEWER_TOKEN_SHA256" in error for error in errors))
        self.assertTrue(any("private gateway" in error for error in errors))
        self.assertTrue(any("VIEWER_OWNER_SCOPE" in error for error in errors))

    def test_public_and_private_credentials_must_differ(self):
        env = self.valid_env()
        env["PRIVATE_SERVICE_TOKEN_SHA256"] = env["VIEWER_TOKEN_SHA256"]
        self.assertTrue(any("independent" in error for error in MODULE.errors_for(env, True)))

    def test_bridge_cannot_target_public_viewer_and_ports_or_mounts_are_rejected(self):
        env = self.valid_env()
        env.update({"DASHBOARD_SITE_URL": "https://viewer.example.test", "PUBLIC_EXPOSED_PORTS": "3000",
                    "CREDENTIAL_MOUNTS": "/root/.env:/secrets"})
        errors = MODULE.errors_for(env, True)
        self.assertTrue(any("public viewer hostname" in error for error in errors))
        self.assertTrue(any("publicly exposed" in error for error in errors))
        self.assertTrue(any("credential files" in error for error in errors))

    def test_insecure_backend_bind_is_rejected(self):
        env = self.valid_env()
        env["PUBLIC_BACKEND_BIND"] = "0.0.0.0:8787"
        self.assertTrue(any("unrestricted" in error for error in MODULE.errors_for(env, True)))

    def test_missing_deployment_boundary_evidence_fails(self):
        env = self.valid_env()
        for key in ("PUBLIC_BACKEND_BIND", "PUBLIC_EXPOSED_PORTS", "CREDENTIAL_MOUNTS"):
            env.pop(key)
        errors = MODULE.errors_for(env, True)
        self.assertEqual(sum(key in " ".join(errors) for key in ("PUBLIC_BACKEND_BIND", "PUBLIC_EXPOSED_PORTS", "CREDENTIAL_MOUNTS")), 3)

    def test_production_example_hosts_and_unproven_tls_fail_closed(self):
        env = self.valid_env()
        env["VIEWER_ORIGIN"] = "https://viewer.example.invalid"
        env["DASHBOARD_SITE_URL"] = "https://bridge.example.invalid"
        errors = MODULE.errors_for(env, local_fixture=False)
        self.assertTrue(any("example-only" in error for error in errors))
        self.assertTrue(any("managed-existing-ingress" in error for error in errors))
        self.assertTrue(any("DNS_PROOF_REF" in error for error in errors))
        self.assertTrue(any("CERT_RENEWAL_PROOF_REF" in error for error in errors))

    def test_credentialed_or_pathful_origins_are_rejected(self):
        env = self.valid_env()
        env["VIEWER_ORIGIN"] = "https://user:password@viewer.example.test/nested"
        env["DASHBOARD_SITE_URL"] = "https://user:password@private-gateway.example.test/path"
        errors = MODULE.errors_for(env, True)
        self.assertTrue(any("exact HTTPS origin" in error for error in errors))
        self.assertTrue(any("credential-free HTTPS private gateway" in error for error in errors))

    def test_example_template_is_not_mistaken_for_deployment_evidence(self):
        env = MODULE.read_env(SCRIPT.parents[2] / "deploy" / "dashboard-public.env.example")
        self.assertTrue(MODULE.errors_for(env, local_fixture=True))


if __name__ == "__main__":
    unittest.main()
