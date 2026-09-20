import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class SharedGrafanaGitOpsTests(unittest.TestCase):
    def test_shared_runtime_is_observability_owned(self):
        manifest = (ROOT / "platform/observability/production/grafana.yaml").read_text()
        self.assertIn("namespace: observability-system", manifest)
        self.assertIn("name: re8ch-grafana", manifest)
        self.assertIn("GF_AUTH_GENERIC_OAUTH_ENABLED", manifest)
        self.assertIn("name: re8ch-grafana-database", manifest)
        self.assertIn("kind: CronJob", manifest)
        self.assertIn("name: re8ch-grafana-org-sync", manifest)
        self.assertNotIn("qianwen-ops", manifest)
        self.assertNotIn("panel-grafana", manifest)

    def test_shared_runtime_bootstraps_owned_dependencies(self):
        credentials = (ROOT / "platform/observability/production/grafana-secrets.sops.yaml").read_text()
        database = (ROOT / "platform/observability/production/grafana-database.yaml").read_text()
        dex = (ROOT / "clusters/re8ch/components/auth/dex/grafana-client.yaml").read_text()
        self.assertIn("ENC[AES256_GCM", credentials)
        self.assertIn("encrypted_regex: ^(data|stringData)$", credentials)
        self.assertNotIn("re8ch-grafana-oidc", credentials)
        self.assertIn("storageClassName: rbd-r640", database)
        self.assertIn("name: re8ch-grafana-postgresql", database)
        self.assertIn("public: true", dex)
        self.assertIn("/grafana/login/generic_oauth", dex)

    def test_new_datasources_do_not_use_legacy_panel_backends(self):
        manifest = (ROOT / "platform/observability/production/dashboards.yaml").read_text()
        self.assertIn("observability-system.svc.cluster.local", manifest)
        self.assertNotIn("panel-victoria-", manifest)
        self.assertNotIn("qianwen-ops", manifest)

    def test_public_grafana_route_and_dns_authority_are_not_qwen_owned(self):
        paths = [
            "clusters/re8ch/components/cilium/gateway-api-canary.yaml",
            "clusters/re8ch/components/public-exposure/grafana-dns-authority.yaml",
            "clusters/re8ch/components/public-exposure/legacy-special-http-routes.yaml",
        ]
        rendered = "\n".join((ROOT / path).read_text() for path in paths)
        self.assertIn("namespace: observability-system", rendered)
        self.assertIn("observability-system/grafana-dns-authority", rendered)
        self.assertNotIn("qianwen-ops/grafana-dns-authority", rendered)
        self.assertNotIn("panel-grafana", rendered)

    def test_grafana_is_a_named_recovery_target(self):
        app = (ROOT / "clusters/re8ch/components/ops/ops-mcp-server-app.py").read_text()
        rbac = (ROOT / "clusters/re8ch/components/toolhive/platform-restart-rbac.yaml").read_text()
        self.assertIn('(\"observability-system\", \"re8ch-grafana\")', app)
        self.assertIn("namespace: observability-system", rbac)
        self.assertIn("resourceNames: [re8ch-grafana]", rbac)


if __name__ == "__main__":
    unittest.main()
