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
        self.assertIn("name: re8ch-grafana-from-gateway", manifest)
        self.assertIn("fromEntities: [ingress]", manifest)
        self.assertIn("kubernetes.io/metadata.name: toolhive-system", manifest)

    def test_shared_runtime_bootstraps_owned_dependencies(self):
        credentials = (ROOT / "platform/observability/production/grafana-secrets.sops.yaml").read_text()
        database = (ROOT / "platform/observability/production/grafana-database.yaml").read_text()
        dex = (ROOT / "clusters/re8ch/components/auth/dex/grafana-client.yaml").read_text()
        self.assertIn("ENC[AES256_GCM", credentials)
        self.assertIn("encrypted_regex: ^(data|stringData)$", credentials)
        self.assertNotIn("re8ch-grafana-oidc", credentials)
        self.assertIn("storageClassName: local-path", database)
        self.assertIn("name: re8ch-grafana-postgresql-data-v2", database)
        self.assertIn("name: re8ch-grafana-postgresql", database)
        self.assertIn("public: true", dex)
        self.assertIn("/grafana/login/generic_oauth", dex)

    def test_new_datasources_do_not_use_legacy_panel_backends(self):
        manifest = (ROOT / "platform/observability/production/dashboards.yaml").read_text()
        self.assertIn("observability-system.svc.cluster.local", manifest)
        self.assertNotIn("panel-victoria-", manifest)
        self.assertNotIn("qianwen-ops", manifest)

    def test_public_grafana_route_and_dns_authority_are_not_qwen_owned(self):
        root = (ROOT / "clusters/re8ch/kustomization.yaml").read_text()
        self.assertIn("components/public-exposure/grafana-dns-authority.yaml", root)
        paths = [
            "clusters/re8ch/components/cilium/gateway-api-canary.yaml",
            "clusters/re8ch/components/public-exposure/grafana-dns-authority.yaml",
            "clusters/re8ch/components/public-exposure/legacy-special-http-routes.yaml",
        ]
        rendered = "\n".join((ROOT / path).read_text() for path in paths)
        self.assertIn("namespace: observability-system", rendered)
        self.assertIn("observability-system/grafana-dns-authority", rendered)
        self.assertIn("external-dns.alpha.kubernetes.io/target: 192.0.2.138", rendered)
        self.assertNotIn("qianwen-ops/grafana-dns-authority", rendered)
        self.assertNotIn("panel-grafana", rendered)

        public_edge_manager = (ROOT / "platform/public-edge-manager/release.yaml").read_text()
        self.assertIn("grafana: {namespace: observability-system, name: grafana-dns-authority}", public_edge_manager)
        self.assertNotIn("grafana: {namespace: qianwen-ops", public_edge_manager)

    def test_grafana_is_a_named_recovery_target(self):
        app = (ROOT / "clusters/re8ch/components/ops/ops-mcp-server-app.py").read_text()
        rbac = (ROOT / "clusters/re8ch/components/toolhive/platform-restart-rbac.yaml").read_text()
        self.assertIn('(\"observability-system\", \"re8ch-grafana\")', app)
        self.assertIn("namespace: observability-system", rbac)
        self.assertIn("resourceNames: [re8ch-grafana]", rbac)

    def test_legacy_qwen_grafana_publication_is_retired_without_deleting_backups(self):
        root = (ROOT / "clusters/re8ch/kustomization.yaml").read_text()
        cleanup_path = "components/observability/retire-legacy-qianwen-grafana.yaml"
        self.assertIn(cleanup_path, root)
        cleanup = (ROOT / "clusters/re8ch" / cleanup_path).read_text()
        for resource in (
            "grafana-dns-authority",
            "panel-grafana-advfab",
            "panel-grafana-renderer",
            "panel-grafana-gateway-origin",
        ):
            self.assertIn(resource, cleanup)
        self.assertNotIn("persistentvolumeclaim", cleanup.lower())
        self.assertNotIn("secret", cleanup.lower())

    def test_observe_probes_target_the_shared_monitoring_plane(self):
        app = (ROOT / "clusters/re8ch/components/ops/ops-mcp-server-app.py").read_text()
        self.assertIn("re8ch-grafana.observability-system.svc.cluster.local:3000/grafana/api/health", app)
        self.assertIn("vmalert-re8ch.observability-system.svc.cluster.local:8080/health", app)
        self.assertIn("re8ch-gateway-collector-monitoring.observability-system.svc.cluster.local:8888/metrics", app)
        self.assertNotIn("trial-gateway-collector-monitoring.observability-ai", app)

    def test_otel_gateway_uses_contrib_distribution_for_remote_write(self):
        manifest = (ROOT / "platform/observability/production/otel.yaml").read_text()
        gateway = manifest.split("---", 1)[0]
        self.assertIn("opentelemetry-collector-contrib:0.153.0@sha256:", gateway)
        self.assertIn("prometheusremotewrite:", gateway)
        self.assertIn("rollingUpdate: {maxSurge: 0, maxUnavailable: 1}", gateway)

    def test_cost_and_observe_paths_use_the_shared_metrics_plane(self):
        paths = [
            "clusters/re8ch/components/toolhive/vmcp-external-publication.yaml",
            "clusters/re8ch/components/network/public-mesh/network-economics.yaml",
            "operations/network/manifests/network/public-mesh/network-economics.yaml",
            "operations/network/manifests/network/public-mesh/opencost-values.yaml",
        ]
        manifests = "\n".join((ROOT / path).read_text() for path in paths)
        self.assertIn("vmselect-re8ch-metrics.observability-system.svc.cluster.local", manifests)
        self.assertNotIn("panel-victoria-metrics.qianwen-ops.svc.cluster.local", manifests)


if __name__ == "__main__":
    unittest.main()
