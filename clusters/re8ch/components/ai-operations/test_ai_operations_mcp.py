import importlib.util
import os
import tempfile
import unittest
import json
import sys
import threading
import urllib.error
import urllib.request
from urllib.request import urlopen as real_urlopen
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))


def load(profile="cloud"):
    root = Path(__file__).parent
    tmp = tempfile.mkdtemp()
    Path(tmp, "nodes.json").write_text(json.dumps([{"id": "restricted-node-6dc3f140f9", "target": "restricted-node-6dc3f140f9", "enabled": True}]))
    Path(tmp, "ssh_config").write_text("Host restricted-node-6dc3f140f9\n  HostName 127.0.0.1\n")
    Path(tmp, "id_ed25519").write_text("test-only")
    Path(tmp, "policy.json").write_text(json.dumps({"revision": 7, "rules": [{"name": "test-node-ops", "actions": ["shell", "file"], "nodes": ["*"], "groups": [], "credential_refs": ["node-ssh"]}]}))
    os.environ.update({"RE8CH_TOOL_PROFILE": profile, "RE8CH_TOOL_AUDIENCE": f"aud-{profile}", "DEVICE_REGISTRY_PATH": f"{tmp}/devices.db", "NODE_INVENTORY_PATH": f"{tmp}/nodes.json", "SSH_CONFIG": f"{tmp}/ssh_config", "SSH_IDENTITY_FILE": f"{tmp}/id_ed25519", "CAPSULE_POLICY_PATH": f"{tmp}/policy.json", "CAPSULE_SIGNING_KEY": "unit-test-signing-key"})
    spec = importlib.util.spec_from_file_location(f"mcp_{profile}", root / "ai_operations_mcp.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class RegistryTests(unittest.TestCase):
    def test_admin_dynamic_skill_tools_forward_review_with_subject(self):
        with patch.dict(os.environ, {"DYNAMIC_SKILL_ADMIN_URL": "http://tenant-api.test/internal/dynamic-skills", "SERVICE_API_TOKEN": "test-secret"}):
            admin = load("admin")
        names = {tool["name"] for tool in admin.tools()}
        self.assertTrue({"dynamic_skill_pending", "dynamic_skill_publish", "dynamic_skill_review", "dynamic_skill_revoke"} <= names)
        tenant = load("tenant")
        self.assertNotIn("dynamic_skill_review", {tool["name"] for tool in tenant.tools()})

        class Reply:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, *_args): return b'{"status":"Published"}'

        def inspect(request, timeout):
            self.assertEqual(timeout, 10)
            self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")
            self.assertEqual(request.get_header("X-re8ch-admin-subject"), "reviewer-1")
            self.assertEqual(json.loads(request.data), {"action": "review", "skillRef": "dynamic-skill://acme/example/v1", "publish": True})
            return Reply()

        with patch.object(admin.urllib.request, "urlopen", side_effect=inspect):
            result = admin.call_tool("dynamic_skill_review", {"skill_ref": "dynamic-skill://acme/example/v1", "publish": True}, {"subject": "reviewer-1"})
        self.assertEqual(result, {"status": "Published"})

    def test_billing_http_requires_supabase_tenant_membership_and_allows_auth_center(self):
        m = load("tenant")
        m.TENANT_SERVICE_DB = Path(tempfile.mkdtemp()) / "billing.db"
        m._TENANT_SERVICE_STORE = None
        event = {
            "eventId": "ue_" + "d" * 32, "tenantRef": "re8ch", "resourceRef": "re8ch-anysite",
            "serviceClass": "compute.workload.v1", "meter": "occupancy.cpu", "quantity": 45,
            "unit": "core-second", "periodStart": "2026-09-29T00:00:00Z",
            "periodEnd": "2026-09-29T00:01:00Z", "regionRef": "loudi",
            "sourceOperationId": "birfucate:test", "observedAt": "2026-09-29T00:01:00Z",
            "schemaVersion": "usage.re8ch.com/v1", "pricingApplied": False, "accountingPosted": False,
        }
        m.tenant_service_store().receive_usage(event)
        m.supabase_identity = lambda token: {"auth_type": "supabase", "subject": "user-1", "_supabase_access_token": token} if token == "valid-token" else None
        m.authenticated_tenant_context = lambda identity: {"tenantRef": "re8ch"} if identity.get("requested_tenant") == "re8ch" else (_ for _ in ()).throw(PermissionError())
        server = ThreadingHTTPServer(("127.0.0.1", 0), m.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/billing/usage"
        try:
            preflight = urllib.request.Request(url, method="OPTIONS", headers={"Origin": "https://auth.re8ch.com"})
            with real_urlopen(preflight, timeout=2) as response:
                self.assertEqual(response.status, 204)
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], "https://auth.re8ch.com")
            headers = {"Origin": "https://auth.re8ch.com", "Authorization": "Bearer valid-token", "X-RE8CH-Tenant": "re8ch"}
            with real_urlopen(urllib.request.Request(url, headers=headers), timeout=2) as response:
                result = json.load(response)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(result["tenantRef"], "re8ch")
            self.assertEqual(result["items"][0]["quantity"], 45)
            self.assertFalse(result["pricingApplied"])
            for denied_headers, expected in [({**headers, "X-RE8CH-Tenant": "other"}, 403),
                                             ({**headers, "Authorization": "Bearer wrong"}, 401)]:
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    real_urlopen(urllib.request.Request(url, headers=denied_headers), timeout=2)
                self.assertEqual(denied.exception.code, expected)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_usage_receiver_authenticates_deduplicates_and_isolates(self):
        m = load("tenant")
        m.TENANT_SERVICE_DB = Path(tempfile.mkdtemp()) / "usage.db"
        m._TENANT_SERVICE_STORE = None
        m.authenticated_usage_collector = lambda header: header == "Bearer test-projected-token"
        server = ThreadingHTTPServer(("127.0.0.1", 0), m.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        event = {
            "eventId": "ue_" + "c" * 32, "tenantRef": "re8ch", "resourceRef": "claim://re8ch/test",
            "serviceClass": "compute.workload.v1", "meter": "occupancy.cpu", "quantity": 60,
            "unit": "core-second", "periodStart": "2026-09-01T00:00:00Z",
            "periodEnd": "2026-09-01T00:01:00Z", "regionRef": "loudi",
            "sourceOperationId": "birfucate:test", "observedAt": "2026-09-01T00:01:00Z",
            "schemaVersion": "usage.re8ch.com/v1", "pricingApplied": False, "accountingPosted": False,
        }
        def post(value, token):
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/internal/usage-events",
                data=json.dumps(value).encode(), headers={"Authorization": token, "Content-Type": "application/json"}, method="POST",
            )
            with real_urlopen(request, timeout=2) as response:
                return response.status, json.load(response)
        try:
            with self.assertRaises(urllib.error.HTTPError) as denied:
                post(event, "Bearer wrong")
            self.assertEqual(denied.exception.code, 401)
            self.assertEqual(post(event, "Bearer test-projected-token")[0], 201)
            status, replay = post(event, "Bearer test-projected-token")
            self.assertEqual(status, 200)
            self.assertTrue(replay["idempotentReplay"])
            collector_path = Path(__file__).parents[2] / "charts/birfucate-metering/files/collector.py"
            collector_spec = importlib.util.spec_from_file_location("usage_collector_test", collector_path)
            collector = importlib.util.module_from_spec(collector_spec)
            collector_spec.loader.exec_module(collector)
            token_path = m.TENANT_SERVICE_DB.parent / "projected-token"
            token_path.write_text("test-projected-token")
            collector.USAGE_TOKEN_PATH = token_path
            outbox = collector.Outbox(m.TENANT_SERVICE_DB.parent / "outbox.db")
            outbox.put(event)
            with patch.object(collector.urllib.request, "urlopen", real_urlopen):
                collector.deliver({"usageSink": {"enabled": True, "url": f"http://127.0.0.1:{server.server_port}/internal/usage-events"}}, outbox)
            self.assertEqual(outbox.pending(), [])
            self.assertEqual(m.tenant_service_store().usage_summary("re8ch")["eventCount"], 1)
            self.assertEqual(m.tenant_service_store().usage_summary("artchais")["eventCount"], 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_tenant_profile_includes_artchais_grant_tools_and_denies_secret_admin_commands(self):
        m = load("tenant")
        self.assertTrue({
            "artc_kubernetes_read", "artc_kubernetes_apply", "artc_kubernetes_admin",
        } <= {tool["name"] for tool in m.tools()})
        self.assertTrue(m.forbidden_admin_argv(["get", "secrets.v1"], None))
        self.assertTrue(m.forbidden_admin_argv(["config", "view"], None))
        self.assertTrue(m.forbidden_admin_argv(["get", "--raw", "/api/v1/secrets"], None))
        sensitive_manifest = "kind: Secret\nstringData:\n  pass" + "word: test-only"
        self.assertTrue(m.forbidden_admin_argv(["apply", "-f", "-"], sensitive_manifest))
        self.assertFalse(m.forbidden_admin_argv(["get", "nodes", "-o", "wide"], None))

    def test_artchais_apply_forces_namespace_and_requires_operator(self):
        m = load("tenant")
        m.authenticated_tenant_context = lambda _identity: {"tenantRef": "artchais", "role": "viewer"}
        workload = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "web"}, "spec": {}}
        with self.assertRaises(PermissionError):
            m.call_artc_cluster_tool("artc_kubernetes_apply", {"object": workload}, {})
        m.authenticated_tenant_context = lambda _identity: {"tenantRef": "artchais", "role": "operator"}
        preview = m.call_artc_cluster_tool("artc_kubernetes_apply", {"object": workload}, {})
        self.assertFalse(preview["applied"])
        self.assertEqual(preview["object"]["metadata"]["namespace"], "artchais")

    def test_observability_profile_exposes_deep_grafana_surface(self):
        m = load("tenant")
        names = {tool["name"] for tool in m.tools()}
        self.assertTrue({
            "grafana_capability_list", "grafana_capability_get",
            "grafana_datasource_list", "grafana_datasource_get", "grafana_query", "grafana_analyze",
            "grafana_indicators", "grafana_correlate", "grafana_render",
            "grafana_exploration_save", "grafana_exploration_delete",
        }.issubset(names))
        self.assertNotIn("grafana_inventory", names)

    def test_grafana_capability_manifest_distinguishes_origins_and_evidence(self):
        load("tenant")
        gx = importlib.import_module("grafana_exploration")
        manifest = gx.grafana_capabilities()
        self.assertTrue(manifest["capabilityManifestHash"].startswith("sha256:"))
        self.assertEqual(manifest["metadata"]["collectionEvidenceStatus"], "not-executed")
        self.assertIsNone(manifest["metadata"]["collectionEvidenceHash"])
        by_id = {item["id"]: item for item in manifest["items"]}
        self.assertEqual(by_id["datasource-query"]["origin"], "grafana-oss")
        self.assertEqual(by_id["jaeger-trace-search"]["origin"], "datasource-plugin")
        self.assertEqual(by_id["numeric-analysis"]["origin"], "re8ch-analysis")
        self.assertEqual(by_id["dashboard-save"]["officialApi"]["generation"], "new-with-legacy-fallback")
        self.assertIn("Does not execute Grafana panel transformations", by_id["numeric-analysis"]["limitations"])
        with self.assertRaises(gx.ExplorationError):
            gx.grafana_capability("does-not-exist")

    def test_grafana_capabilities_are_a_tenant_scoped_mcp_resource(self):
        m = load("tenant")
        m.TENANT_SERVICE_DB = Path(tempfile.mkdtemp()) / "tenant.db"
        m._TENANT_SERVICE_STORE = None
        m.authenticated_tenant = lambda _identity: "acme"
        m.tenant_control_request = lambda *_args, **_kwargs: {"items": []}
        m.tenant_claim_usage_summary = lambda *_args, **_kwargs: {"claims": []}
        identity = {"tenant": "acme", "groups": []}
        uri = "re8ch://tenant/acme/grafana/capabilities"
        self.assertIn(uri, {item["uri"] for item in m.resources_list(identity, None)["resources"]})
        body = json.loads(m.resources_read(identity, uri)["contents"][0]["text"])
        self.assertEqual(body["kind"], "GrafanaCapabilityList")

    def test_resource_list_does_not_fetch_resource_contents(self):
        m = load("tenant")
        m.authenticated_tenant = lambda _identity: "acme"
        def unexpected_fetch(*_args, **_kwargs):
            self.fail("resources/list must not fetch resource contents")
        m.tenant_service_store = unexpected_fetch
        m.tenant_control_request = unexpected_fetch
        m.tenant_claim_usage_summary = unexpected_fetch
        m.grafana_capabilities = unexpected_fetch
        listed = m.resources_list({"subject": "user-1"}, None)
        self.assertIn("re8ch://tenant/acme/claims", {item["uri"] for item in listed["resources"]})

    def test_grafana_query_requires_tenant_scope_and_rejects_sql_writes(self):
        m = load("tenant")
        gx = importlib.import_module("grafana_exploration")
        base = {"timeRange": {"from": 1_000, "to": 2_000}, "queries": [{"datasourceUid": "metrics", "refId": "A", "queryKind": "promql", "model": {"expr": 'up{tenant="qianwen"}'}}]}
        with patch.dict(os.environ, {"GRAFANA_TENANT_BINDINGS_JSON": json.dumps({"qwen": {"orgId": 4, "tenantLabel": "qianwen", "tokenEnv": "TEST_GRAFANA_TOKEN"}})}):
            gx.validate_query_spec(base, "qwen", {"metrics": "prometheus"})
            bad = json.loads(json.dumps(base)); bad["queries"][0]["model"]["expr"] = "up"
            with self.assertRaises(gx.ExplorationError):
                gx.validate_query_spec(bad, "qwen", {"metrics": "prometheus"})
        with patch.dict(os.environ, {"GRAFANA_TENANT_BINDINGS_JSON": ""}):
            with self.assertRaises(gx.ExplorationError):
                gx.tenant_config("qwen")
        with self.assertRaises(gx.ExplorationError):
            gx.validate_sql("DELETE FROM events")
        with self.assertRaises(gx.ExplorationError):
            gx.validate_sql("SELECT 1; DROP TABLE events")

    def test_grafana_query_uses_datasource_minimum_interval(self):
        load("tenant")
        gx = importlib.import_module("grafana_exploration")
        class Client:
            body = None
            def request(self, method, path, body):
                self.body = body
                return {"results": {"A": {"frames": []}}}
        client = Client()
        spec = {"timeRange": {"from": 1_000, "to": 61_000}, "queries": [{"datasourceUid": "metrics", "refId": "A", "queryKind": "promql", "model": {"expr": 'up{tenant="qianwen"}'}, "maxDataPoints": 1000}]}
        with patch.dict(os.environ, {"GRAFANA_TENANT_BINDINGS_JSON": json.dumps({"qwen": {"orgId": 4, "tenantLabel": "qianwen", "tokenEnv": "TEST_GRAFANA_TOKEN"}})}):
            gx.execute_query(client, "qwen", spec, [{"uid": "metrics", "type": "prometheus", "jsonData": {"timeInterval": "15s"}}])
            self.assertEqual(client.body["queries"][0]["intervalMs"], 15_000)
            spec["queries"][0]["intervalMs"] = 1000
            with self.assertRaises(gx.ExplorationError):
                gx.execute_query(client, "qwen", spec, [{"uid": "metrics", "type": "prometheus", "jsonData": {"timeInterval": "15s"}}])

    def test_grafana_indicators_and_correlations_are_deterministic(self):
        load("tenant")
        gx = importlib.import_module("grafana_exploration")
        result = gx.indicators([1.0, 2.0, 3.0, 4.0], [{"type": "movingAverage", "window": 2}, {"type": "slope"}, {"type": "volatility", "window": 3}])
        self.assertEqual(result["movingAverage"], [1.0, 1.5, 2.5, 3.5])
        self.assertEqual(result["slope"]["trend"], "up")
        self.assertAlmostEqual(gx.correlation([1, 2, 3], [2, 4, 6]), 1.0)
        self.assertAlmostEqual(gx.correlation([10, 20, 30], [1, 2, 3], "spearman"), 1.0)

    def test_grafana_result_references_are_tenant_bound(self):
        load("tenant")
        gx = importlib.import_module("grafana_exploration")
        ref = gx.RESULTS.put("qwen", {"series": [1, 2]})
        self.assertEqual(gx.RESULTS.get("qwen", ref)["series"], [1, 2])
        with self.assertRaises(gx.ExplorationError):
            gx.RESULTS.get("ledger", ref)

    def test_permanent_token_is_audience_bound_and_deactivatable(self):
        m = load("cloud")
        meta, token = m.registry().activate("user-1", [m.ADMIN_GROUP], "ssh-ed25519 test")
        self.assertIsNone(meta.get("expires_at"))
        self.assertEqual(m.registry().authenticate(token)["device_id"], meta["device_id"])
        self.assertTrue(m.registry().deactivate(meta["device_id"], "user-1")["deactivated"])
        self.assertIsNone(m.registry().authenticate(token))

    def test_profiles_do_not_leak_cloud_tools(self):
        m = load("orchestra")
        names = {tool["name"] for tool in m.tools()}
        self.assertIn("orchestra_request", names)
        self.assertNotIn("kubectl_exec", names)
        self.assertNotIn("ssh_exec", names)

    def test_infra_exposes_litellm_toolset(self):
        m = load("infra")
        names = {tool["name"] for tool in m.tools()}
        self.assertIn("litellm_gateway_status", names)
        self.assertIn("litellm_models", names)
        self.assertIn("litellm_spend", names)
        infra_observe = next(tool for tool in m.tools() if tool["name"] == "infra_observe")
        self.assertIn("litellm", infra_observe["inputSchema"]["properties"]["domain"]["enum"])

    def test_infra_exposes_headlamp_and_storage_views(self):
        m = load("infra")
        names = {tool["name"] for tool in m.tools()}
        self.assertTrue({
            "mihomos_cluster_status", "public_edge_status", "advanced_fabric_status",
            "cilium_helm_app_status", "rook_csi_app_status",
        }.issubset(names))

    def test_infra_exposes_native_harbor_tools(self):
        m = load("infra")
        names = {tool["name"] for tool in m.tools()}
        self.assertTrue({"harbor_users", "harbor_projects", "harbor_tenant_reconcile", "harbor_robot_disable"}.issubset(names))
        reconcile = next(tool for tool in m.tools() if tool["name"] == "harbor_tenant_reconcile")
        serialized = json.dumps(reconcile)
        self.assertNotIn("password", serialized.lower())
        self.assertNotIn("token", serialized.lower())

    def test_infra_exposes_native_grafana_and_headlamp_kubernetes_views(self):
        m = load("infra")
        names = {tool["name"] for tool in m.tools()}
        self.assertTrue({"grafana_inventory", "grafana_organization_get", "kubernetes_workload_inventory", "kubernetes_recent_events"}.issubset(names))

    def test_harbor_admin_credential_falls_back_to_server_held_secret(self):
        m = load("infra")
        m.HARBOR_URL = "https://registry.example"
        m.os.environ.pop("HARBOR_ADMIN_USERNAME", None)
        m.os.environ.pop("HARBOR_ADMIN_PASSWORD", None)
        values = {("ai-operations", "cluster-infra-harbor", "username"): "admin", ("ai-operations", "cluster-infra-harbor", "password"): "opaque"}
        m.kubernetes_secret_value = lambda namespace, name, key: values[(namespace, name, key)]
        client = m.harbor_admin_client()
        self.assertEqual(client.base_url, "https://registry.example/api/v2.0")

    def test_tenant_profile_exposes_only_tenant_harbor_surface(self):
        m = load("tenant")
        names = {tool["name"] for tool in m.tools()}
        self.assertIn("harbor_project_get", names)
        self.assertNotIn("harbor_users", names)
        self.assertNotIn("infra_execute", names)

    def test_admin_and_tenant_profiles_are_separate(self):
        m = load("tenant")
        names = {tool["name"] for tool in m.tools()}
        self.assertTrue({"service_catalog_list", "service_instance_create", "service_binding_create", "usage_summary_get", "operation_get", "tenant_claim_create"}.issubset(names))
        self.assertNotIn("tenant_create", names)
        self.assertNotIn("infra_execute", names)
        self.assertNotIn("harbor_users", names)
        self.assertNotIn("activate_device", names)
        admin = load("admin")
        admin_names = {tool["name"] for tool in admin.tools()}
        self.assertTrue({"tenant_create", "tenant_delete"}.issubset(admin_names))
        self.assertNotIn("tenant_grant_create", admin_names)
        self.assertNotIn("tenant_claim_create", admin_names)

    def test_tenant_planes_are_server_side_isolated_and_resources_are_tenant_scoped(self):
        registry = load("tenant")
        registry.TENANT_SERVICE_DB = Path(tempfile.mkdtemp()) / "tenant.db"
        registry._TENANT_SERVICE_STORE = None
        names = {tool["name"] for tool in registry.tools()}
        self.assertIn("service_instance_create", names)
        self.assertTrue({"harbor_project_get", "harbor_artifact_scan_start", "harbor_sbom_get", "harbor_build_start"}.issubset(names))
        self.assertNotIn("tenant_create", names)
        self.assertIn("byoc_cluster_register", names)
        registry.authenticated_tenant = lambda _identity: "acme"
        registry.authenticated_tenant_grants = lambda _identity: [
            {"consumableRef": "registry.harbor.v1", "lifecycle": "Active"},
            {"consumableRef": "observability.grafana.v1", "lifecycle": "Active"},
        ]
        registry.tenant_claim_usage_summary = lambda *_args, **_kwargs: {"claims": []}
        identity = {"tenant": "acme", "groups": []}
        listed = registry.resources_list(identity, None)
        self.assertTrue(all("/acme/" in item["uri"] for item in listed["resources"]))
        catalog = registry.resources_read(identity, "re8ch://tenant/acme/catalog")
        body = json.loads(catalog["contents"][0]["text"])
        self.assertTrue({"registry.harbor.v1", "observability.grafana.v1"} <= {item["serviceClass"] for item in body["services"]})
        self.assertNotIn("network.among-clusters.v1", {item["serviceClass"] for item in body["services"]})
        with self.assertRaises(PermissionError):
            registry.resources_read(identity, "re8ch://tenant/other/instances")

    def test_byoc_registration_returns_opaque_reference_and_hashes_key(self):
        byoc = load("tenant")
        byoc.TENANT_SERVICE_DB = Path(tempfile.mkdtemp()) / "tenant.db"
        byoc._TENANT_SERVICE_STORE = None
        identity = {"tenant": "acme", "groups": []}
        byoc.authenticated_tenant = lambda _identity: "acme"
        result = byoc.call_tool("byoc_cluster_register", {"cluster_uid": "cluster-uid-123", "region_ref": "cn-east", "issuer": "https://issuer.example", "public_key": "public-key", "capabilities": ["mcs"]}, identity)
        self.assertTrue(result["enrollmentRef"].startswith("enrollment://"))
        stored = byoc.tenant_service_store().registrations("acme")[0]
        self.assertNotEqual(stored["public_key_hash"], "public-key")
        self.assertTrue(stored["public_key_hash"].startswith("sha256:"))

    def test_tenant_context_never_uses_group_or_tenant_claims(self):
        m = load("tenant")
        with self.assertRaises(PermissionError):
            m.authenticated_tenant({"tenant": "research-a", "groups": ["re8ch:tenant:research-a"]})

    def test_tenant_context_uses_admin_managed_oidc_identity_binding(self):
        m = load("tenant")
        m.TENANT_CONTROL_URL = "http://tenant-control"
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return None
            def read(self): return b'{"tenantRef":"research-a","runtimeNamespaceRef":"research-a"}'
        m.urllib.request.urlopen = lambda *_args, **_kwargs: Response()
        identity = {
            "auth_type": "dex", "issuer": "https://auth.re8ch.com",
            "subject": "subject-1", "groups": [],
        }
        self.assertEqual(m.authenticated_tenant(identity), "research-a")

    def test_admin_requires_admin_group_even_when_dex_is_authenticated(self):
        m = load("admin")
        self.assertFalse(m.allowed({"auth_type": "dex", "subject": "user", "groups": []}))
        self.assertTrue(m.allowed({"auth_type": "dex", "subject": "admin", "groups": ["re8ch:tenant-admins"]}))

    def test_litellm_uses_its_real_health_path(self):
        m = load("orchestra")
        self.assertEqual(m.ORCHESTRA_HEALTH_PATHS["litellm"], "/health/liveliness")

    def test_cross_audience_token_rejected(self):
        cloud = load("cloud")
        _, token = cloud.registry().activate("user-1", [cloud.ADMIN_GROUP], None)
        infra = load("infra")
        self.assertIsNone(infra.registry().authenticate(token))

    def test_service_token_is_profile_scoped(self):
        m = load("infra")
        m.SERVICE_API_TOKEN = "service-test-token"
        handler = object.__new__(m.Handler)
        handler.headers = {"Authorization": "Bearer service-test-token"}
        identity = handler.identity()
        self.assertEqual(identity["subject"], "service:litellm")
        self.assertTrue(m.allowed(identity))

        tenant = load("tenant")
        tenant.SERVICE_API_TOKEN = "tenant-service-test-token"
        tenant_handler = object.__new__(tenant.Handler)
        tenant_handler.headers = {"Authorization": "Bearer tenant-service-test-token"}
        self.assertTrue(tenant.allowed(tenant_handler.identity()))

    def test_vmcp_identity_exchange_is_signed_short_lived_and_audience_bound(self):
        tenant = load("tenant")
        tenant.SERVICE_API_TOKEN = "unit-test-exchange-key"
        identity = {
            "subject": "dex-subject-1",
            "issuer": "https://auth.re8ch.com",
            "groups": ["Artchais", "re8ch:tenant-users"],
            "auth_type": "dex",
        }
        token, expires_in = tenant.mint_vmcp_identity_token(identity)
        self.assertTrue(token.startswith("r8x_"))
        self.assertEqual(expires_in, 300)
        self.assertEqual(
            tenant.vmcp_exchange_identity(token),
            {
                "subject": "dex-subject-1",
                "issuer": "https://auth.re8ch.com",
                "groups": ["Artchais", "re8ch:tenant-users"],
                "auth_type": "dex",
            },
        )
        self.assertIsNone(tenant.vmcp_exchange_identity(token + "tampered"))
        tenant.AUDIENCE = "another-audience"
        self.assertIsNone(tenant.vmcp_exchange_identity(token))

    def test_vmcp_token_exchange_validates_subject_token_and_target(self):
        tenant = load("tenant")
        import base64
        import json
        import time
        claims = {"exp": int(time.time()) + 300}
        subject_token = "header." + base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode() + ".signature"
        tenant.supabase_identity = lambda token: {"subject": "supabase-user", "auth_type": "supabase"} if token == subject_token else None
        form = {
            "grant_type": ["urn:ietf:params:oauth:grant-type:token-exchange"],
            "subject_token_type": ["urn:ietf:params:oauth:token-type:access_token"],
            "subject_token": [subject_token],
            "audience": ["aud-tenant"],
        }
        exchanged = tenant.exchange_vmcp_subject_token(form)
        self.assertEqual(exchanged["token_type"], "Bearer")
        self.assertEqual(exchanged["access_token"], subject_token)
        form["subject_token"] = ["incoming-dex-token"]
        with self.assertRaises(PermissionError):
            tenant.exchange_vmcp_subject_token(form)
        form["subject_token"] = [subject_token]
        form["audience"] = ["wrong-audience"]
        with self.assertRaises(PermissionError):
            tenant.exchange_vmcp_subject_token(form)

    def test_tenant_vmcp_service_health_lists_no_tenant_resources(self):
        tenant = load("tenant")
        identity = {"subject": "service:litellm", "groups": [], "auth_type": "service"}
        tenant.authenticated_tenant = lambda _identity: self.fail("service health must not resolve a tenant")
        self.assertEqual(tenant.resources_list(identity, None), {"resources": []})

    def test_capsule_is_signed_one_time_and_broker_hides_material(self):
        m = load("cloud")
        identity = {"subject": "user-1", "groups": [], "device_id": "dev-test", "auth_type": "device"}
        signed = m.create_capsule(identity, {"action": "shell", "node": "restricted-node-6dc3f140f9", "command": "hostname", "credential_refs": ["node-ssh"]})
        m.command_result = lambda argv, stdin, timeout, *, capture_output: {"operation_id": "op-test", "exit_code": 0, "output_suppressed": not capture_output}
        result = m.execute_capsule(identity, signed["capsule"], signed["signature"])
        self.assertEqual(result["exit_code"], 0)
        self.assertFalse(result["credential_material_exposed"])
        self.assertTrue(result["output_suppressed"])
        self.assertNotIn("stdout", result)
        self.assertNotIn("stderr", result)
        self.assertNotIn("paths", result)
        with self.assertRaises(PermissionError):
            m.execute_capsule(identity, signed["capsule"], signed["signature"])

    def test_tampered_capsule_is_rejected(self):
        m = load("cloud")
        identity = {"subject": "user-1", "groups": [], "device_id": "dev-test", "auth_type": "device"}
        signed = m.create_capsule(identity, {"action": "shell", "node": "restricted-node-6dc3f140f9", "command": "hostname", "credential_refs": ["node-ssh"]})
        signed["capsule"]["command"] = "id"
        with self.assertRaises(PermissionError):
            m.execute_capsule(identity, signed["capsule"], signed["signature"])

    def test_capsule_command_discards_output_before_operation_storage(self):
        m = load("cloud")
        secret = "sensitive-credential-material"
        completed = type("Completed", (), {"returncode": 0, "stdout": None, "stderr": None})()
        with patch.object(m.subprocess, "run", return_value=completed) as run:
            result = m.command_result(["ssh", "node", "secret-command"], secret, 30, capture_output=False)
        self.assertEqual(run.call_args.kwargs["stdout"], m.subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["stderr"], m.subprocess.DEVNULL)
        self.assertTrue(result["output_suppressed"])
        for value in (result, m.OPERATIONS[result["operation_id"]]):
            self.assertNotIn("argv", value)
            self.assertNotIn("stdout", value)
            self.assertNotIn("stderr", value)
            self.assertNotIn(secret, json.dumps(value))


if __name__ == "__main__":
    unittest.main()
