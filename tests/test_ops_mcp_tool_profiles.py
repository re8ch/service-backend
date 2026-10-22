import importlib.util
import base64
import json
import os
from pathlib import Path
import unittest


APP = Path(__file__).parents[1] / "clusters/re8ch/components/ops/ops-mcp-server-app.py"


def load_profile(profile):
    os.environ["OPS_TOOL_PROFILE"] = profile
    for name in ("TENANT_NAMESPACE", "TENANT_NAMESPACES", "TENANT_DB_NAMES", "TENANT_METRICS_TENANT"):
        os.environ.pop(name, None)
    spec = importlib.util.spec_from_file_location("ops_mcp_%s" % profile, APP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ToolProfileTests(unittest.TestCase):
    def tools(self, profile):
        return {tool["name"]: tool for tool in load_profile(profile).mcp_tools(include_admin=True)}

    def test_database_health_checks_http_status_instead_of_only_open_port(self):
        module = load_profile("observe")
        module.current_catalog = lambda: {}
        module.probe_tcp = lambda name, domain, *args: {"name": name, "domain": domain, "status": "reachable"}
        module.probe_http = lambda name, domain, *args: {"name": name, "domain": domain, "status": "unhealthy" if name.startswith("pigsty-") else "healthy"}
        result = module.get_platform_service_health()
        routes = {row["name"]: row for row in result["services"] if row["name"].startswith("pigsty-")}
        self.assertEqual(routes["pigsty-postgrestWrite"]["status"], "unhealthy")
        self.assertEqual(routes["pigsty-postgrestRead"]["status"], "unhealthy")
        self.assertEqual(routes["pigsty-writeService"]["status"], "reachable")
        self.assertEqual(result["summary"]["unreachable"], 2)

    def test_trusted_proxy_propagates_allowlisted_tenant_to_metrics(self):
        module = load_profile("observe")
        module.TENANT_CONTEXT_ALLOWLIST.add("re8ch")
        request = type("Request", (), {})()
        request.headers = {"X-Re8ch-Tenant": "re8ch"}
        request.trusted_loopback_proxy = lambda: True
        request.token_tenant = lambda: None
        tenant = module.Handler.request_tenant_context(request)
        token = module.ACTIVE_TENANT.set(tenant)
        try:
            self.assertEqual(module.current_metrics_tenant(), "re8ch")
            self.assertEqual(module.current_namespaces(), ["re8ch"])
        finally:
            module.ACTIVE_TENANT.reset(token)

    def test_trusted_proxy_rejects_unlisted_tenant_header(self):
        module = load_profile("observe")
        request = type("Request", (), {})()
        request.headers = {"X-Re8ch-Tenant": "other"}
        request.trusted_loopback_proxy = lambda: True
        request.token_tenant = lambda: None
        self.assertEqual(module.Handler.request_tenant_context(request), "platform")

    def test_database_credentials_and_plan_use_the_same_live_role_services(self):
        from urllib.parse import urlsplit
        module = load_profile("admin")
        passwords = {role: "test-only-punctuation:@/" for role in ("admin", "migrator", "app_rw", "app_ro", "pgrst")}
        values = module.build_database_secret_values("example", passwords)
        for field in ("DATABASE_URL_RW", "DATABASE_URL_MIGRATOR", "PGRST_DB_URI"):
            url = urlsplit(values[field])
            self.assertEqual(url.hostname, module.PG_SERVICE_ROUTES["writeService"]["host"])
            self.assertEqual(url.port, 5432)
        read = urlsplit(values["DATABASE_URL_RO"])
        self.assertEqual(read.hostname, module.PG_SERVICE_ROUTES["readService"]["host"])
        self.assertEqual(read.port, 5432)
        self.assertEqual(urlsplit(values["DATABASE_URL_POOL_RW"]).port, 6432)
        self.assertEqual(module.pg_route_from_args({}, read_only=True)[2], "readService")
        self.assertEqual(module.pg_route_from_args({}, read_only=False)[2], "writeService")
        plan = module.generate_pg_tenant_database_plan({"database": "example"})
        self.assertEqual(plan["connectionRoutes"]["recommendedRead"], read.hostname + ":5432")
        self.assertNotIn("5433", plan["psqlCommand"])

    def test_database_secret_values_stay_inside_sql_proxy(self):
        module = load_profile("admin")
        legacy = load_profile("legacy")
        proxy_schema = next(tool["inputSchema"] for tool in legacy.mcp_tools() if tool["name"] == "postgres_sql_proxy")
        self.assertNotIn("user", proxy_schema["properties"])
        self.assertNotIn("password", proxy_schema["properties"])
        module.current_namespace = lambda: "tenant-a"
        module.current_tenant = lambda: "tenant-a"
        module.allowed_databases_for_namespace = lambda _namespace: ["app"]
        module.get_database_secret_reference = lambda namespace, database: {"namespace": namespace, "name": "app-db-credentials"}
        secret = "test-only-db-password"
        doc = {"metadata": {"name": "app-db-credentials", "namespace": "tenant-a", "labels": {
            "re8ch.com/tenant": "tenant-a", "re8ch.com/secret-purpose": "postgres-credentials", "re8ch.com/database": "app",
        }, "annotations": {"re8ch.com/credential-status": "complete"}}, "data": {
            "APP_APP_RO_USER": base64.b64encode(b"app_app_ro").decode(),
            "APP_APP_RO_PASSWORD": base64.b64encode(secret.encode()).decode(),
        }}
        module.kube_request = lambda method, path: doc
        metadata = module.get_tenant_database_credentials({"database": "app"})
        self.assertEqual(metadata["credentialMaterialReturned"], False)
        self.assertEqual(metadata["metadata"]["keys"], ["APP_APP_RO_PASSWORD", "APP_APP_RO_USER"])
        self.assertNotIn(secret, json.dumps(metadata))
        self.assertNotIn("data", metadata)
        self.assertNotIn("recommendedRoutes", metadata)
        with self.assertRaisesRegex(ValueError, "direct database credentials"):
            module.postgres_sql_proxy({"database": "app", "sql": "SELECT 1", "password": secret})
        with self.assertRaisesRegex(PermissionError, "role is not published"):
            module.postgres_sql_proxy({"database": "app", "sql": "SELECT 1", "role": "admin"})
        invoked = []
        module.pg_connect = lambda host, port, database, user, password, timeout: invoked.append((user, password)) or type("Socket", (), {"close": lambda self: None})()
        module.pg_execute_simple = lambda *_args, **_kwargs: {"rows": []}
        result = module.postgres_sql_proxy({"database": "app", "sql": "SELECT 1"})
        self.assertEqual(invoked, [("app_app_ro", secret)])
        for key in ("data", "password", "user", "host", "port"):
            self.assertNotIn(key, result)
        self.assertNotIn(secret, json.dumps(result))

    def test_profiles_are_mutually_exclusive(self):
        observe = set(self.tools("observe"))
        operate = set(self.tools("operate"))
        admin = set(self.tools("admin"))
        self.assertTrue(observe)
        self.assertEqual(
            operate,
            {"get_managed_platform_operation_status", "restart_managed_platform_deployment", "scale_managed_platform_deployment", "get_ceph_recovery_status", "restart_ceph_osd_1"},
        )
        self.assertFalse(observe & operate)
        self.assertFalse(observe & admin)
        self.assertFalse(operate & admin)

    def test_observe_is_read_only(self):
        for tool in self.tools("observe").values():
            self.assertTrue(tool["annotations"]["readOnlyHint"], tool["name"])
            self.assertNotIn("secret", tool["name"].lower())
            self.assertNotIn("sql_proxy", tool["name"])
            self.assertNotIn("kubeconfig", tool["name"])
        self.assertIn("get_ceph_storage_status", self.tools("observe"))
        self.assertIn("get_ready_node_storage_inventory", self.tools("observe"))
        self.assertIn("get_runtime_storage_analyses", self.tools("observe"))

    def test_runtime_analysis_summary_excludes_raw_content_evidence(self):
        module = load_profile("observe")
        module.kube_request = lambda method, path: {"items": [{
            "metadata": {"namespace": "tenant-a", "name": "call-1", "creationTimestamp": "2026-09-23T00:00:00Z"},
            "spec": {"target": {"podName": "worker", "containerName": "app"}, "traceId": "a" * 32},
            "status": {"phase": "Ready", "summary": {"eventCount": 2}, "topLocators": ["/workspace/a"],
                       "evidence": {"digest": "sha256:test", "configMap": "contains-raw-events"}},
        }]}
        result = module.get_runtime_storage_analyses({})
        self.assertEqual(result["analyses"][0]["evidenceDigest"], "sha256:test")
        self.assertNotIn("evidence", result["analyses"][0])
        self.assertNotIn("events", result["analyses"][0])

    def test_storage_inventory_filters_unready_nodes_and_missing_metrics(self):
        module = load_profile("observe")
        module.kube_request = lambda method, path: {"items": [
            {"metadata": {"name": "ready-a"}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}},
            {"metadata": {"name": "down-b"}, "status": {"conditions": [{"type": "Ready", "status": "False"}]}},
            {"metadata": {"name": "ready-c"}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}},
        ]}
        def metric(name, node, value):
            labels = {"node": node}
            if name.startswith("node_filesystem_"):
                labels.update({"mountpoint": "/", "device": "/dev/sda", "fstype": "ext4"})
            return (labels, value, 100)
        rows = {
            "node_filesystem_size_bytes": [metric("node_filesystem_size_bytes", "ready-a", 1000), metric("node_filesystem_size_bytes", "down-b", 1000)],
            "node_filesystem_avail_bytes": [metric("node_filesystem_avail_bytes", "ready-a", 250), metric("node_filesystem_avail_bytes", "down-b", 100)],
            "node_filesystem_readonly": [metric("node_filesystem_readonly", "ready-a", 0)],
            "node_filesystem_device_error": [],
            "node_memory_SwapTotal_bytes": [metric("node_memory_SwapTotal_bytes", "ready-a", 100)],
            "node_memory_SwapFree_bytes": [metric("node_memory_SwapFree_bytes", "ready-a", 40)],
        }
        module._fresh_metric_rows = lambda expr: rows[expr.split("{")[0]]
        result = module.get_ready_node_storage_inventory()
        self.assertEqual([row["node"] for row in result["nodes"]], ["ready-a", "ready-c"])
        self.assertEqual(result["nodes"][0]["filesystems"][0]["usedPercent"], 75.0)
        self.assertEqual(result["nodes"][0]["swap"]["usedPercent"], 60.0)
        self.assertFalse(result["nodes"][1]["metricsAvailable"])

    def test_ceph_status_does_not_call_missing_metrics_healthy(self):
        module = load_profile("observe")
        module.kube_request = lambda method, path: {"status": {"phase": "Ready"}}
        module._fresh_metric_rows = lambda expr: []
        result = module.get_ceph_storage_status()
        self.assertIsNone(result["healthCode"])
        self.assertIsNone(result["placementGroups"]["clean"])
        self.assertFalse(result["metricsFresh"])

    def test_platform_flux_status_separates_frozen_and_conflicting_owners(self):
        module = load_profile("observe")
        releases = [
            {"kind": "HelmRelease", "metadata": {"name": "demo", "namespace": "flux-system", "generation": 2},
             "spec": {"releaseName": "demo", "targetNamespace": "demo", "storageNamespace": "demo", "suspend": True},
             "status": {"observedGeneration": 2, "conditions": [{"type": "Ready", "status": "False", "message": "Suspended"}]}},
            {"kind": "HelmRelease", "metadata": {"name": "demo", "namespace": "flux-agent-system", "generation": 2},
             "spec": {"releaseName": "demo", "targetNamespace": "demo", "storageNamespace": "demo"},
             "status": {"observedGeneration": 2, "conditions": [{"type": "Ready", "status": "True"}]}},
        ]
        module.kube_request = lambda method, path: {"items": [
            row for row in releases
            if path.endswith("/helmreleases") and "/namespaces/%s/" % row["metadata"]["namespace"] in path
        ]}
        result = module.platform_flux_status()
        self.assertEqual(result["summary"]["failed"], 0)
        self.assertEqual(result["summary"]["frozen"], 1)
        self.assertEqual(result["summary"]["ownershipConflicts"], 0)

        releases[0]["spec"]["suspend"] = False
        result = module.platform_flux_status()
        self.assertEqual(result["summary"]["ownershipConflicts"], 1)
        self.assertEqual({row["ownerState"] for row in result["resources"]}, {"conflict"})

    def test_platform_status_upstream_failure_is_not_reported_as_zero_healthy(self):
        module = load_profile("observe")
        module.urllib.request.urlopen = lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError())
        result = module._platform_service_status("example", "http://example.invalid/v1/status")
        self.assertEqual(result["availability"], "unavailable")
        self.assertNotIn("status", result)
        self.assertNotIn("url", json.dumps(result).lower())

    def test_each_profile_advertises_dex_oidc_scope(self):
        for profile in ("observe", "operate", "admin"):
            for tool in self.tools(profile).values():
                self.assertEqual(
                    tool["securitySchemes"],
                    [{"type": "oauth2", "scopes": ["openid"]}],
                    tool["name"],
                )

    def test_operate_write_schema_has_no_approval_id(self):
        tools = self.tools("operate")
        for name in ("restart_managed_platform_deployment", "scale_managed_platform_deployment"):
            self.assertFalse(tools[name]["annotations"]["readOnlyHint"])
            self.assertNotIn("approvalId", tools[name]["inputSchema"]["properties"])
        replicas = tools["scale_managed_platform_deployment"]["inputSchema"]["properties"]["replicas"]
        self.assertEqual(replicas["enum"], [0, 1])
        self.assertTrue(tools["get_ceph_recovery_status"]["annotations"]["readOnlyHint"])
        self.assertFalse(tools["restart_ceph_osd_1"]["annotations"]["readOnlyHint"])

    def test_ceph_recovery_refuses_unready_host(self):
        module = load_profile("operate")
        module.get_ceph_recovery_status = lambda: {
            "desired": 1, "ready": 0, "pods": [{"node": "restricted-node-1eb0570f74"}],
        }
        calls = []

        def fake_request(method, path, body=None, content_type="application/json"):
            calls.append((method, path))
            return {"status": {"conditions": [{"type": "Ready", "status": "False"}]}}

        module.kube_request = fake_request
        with self.assertRaisesRegex(ValueError, "not Ready"):
            module.restart_ceph_osd_1()
        self.assertEqual(calls, [("GET", "/api/v1/nodes/restricted-node-1eb0570f74")])

    def test_ceph_recovery_only_patches_reviewed_osd(self):
        module = load_profile("operate")
        module.get_ceph_recovery_status = lambda: {
            "desired": 1, "ready": 0, "pods": [{"node": "restricted-node-1eb0570f74"}],
        }
        calls = []

        def fake_request(method, path, body=None, content_type="application/json"):
            calls.append((method, path))
            if method == "GET":
                return {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}
            return {"metadata": {"generation": 9}}

        module.kube_request = fake_request
        result = module.restart_ceph_osd_1()
        self.assertEqual(result["deployment"], "rook-ceph-osd-1")
        self.assertEqual(calls, [
            ("GET", "/api/v1/nodes/restricted-node-1eb0570f74"),
            ("PATCH", "/apis/apps/v1/namespaces/rook-ceph/deployments/rook-ceph-osd-1"),
        ])

    def test_admin_uses_dex_and_excludes_authentik_and_secret_values(self):
        admin = set(self.tools("admin"))
        self.assertIn("list_dex_users", admin)
        self.assertIn("create_dex_user", admin)
        self.assertIn("grant_user_namespace", admin)
        self.assertFalse(any("authentik" in name for name in admin))
        self.assertNotIn("get_kubeconfig", admin)
        self.assertNotIn("postgres_sql_proxy", admin)
        self.assertIn("postgres_admin_sql", admin)
        self.assertNotIn("postgres_admin_sql", self.tools("observe"))
        self.assertNotIn("postgres_admin_sql", self.tools("operate"))

    def test_admin_sql_requires_plan_and_keeps_supabase_credential_server_side(self):
        module = load_profile("admin")
        sql = "CREATE TABLE billing_probe (id bigint)"
        args = {"database": "re8ch_billing", "sql": sql, "readOnly": False,
                "approvalId": "re8ch/service-backend#1670"}
        requests = []
        module.kube_request = lambda *request: requests.append(request) or {"data": {
            "POSTGRES_PASSWORD": base64.b64encode(b"test-only-password").decode(),
        }}
        plan = module.postgres_admin_sql(args)
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(requests, [])
        self.assertNotIn("test-only-password", json.dumps(plan))
        with self.assertRaisesRegex(PermissionError, "matching planHash"):
            module.postgres_admin_sql({**args, "apply": True, "planHash": "incorrect"})
        with self.assertRaisesRegex(PermissionError, "admin token required"):
            module.call_tool("postgres_admin_sql", args, admin=False)
        calls = []
        class FakeSocket:
            def close(self):
                pass
        module.pg_connect = lambda *values, **_kw: calls.append(("connect", values)) or FakeSocket()
        module.pg_execute_simple = lambda _sock, statement, **_kw: calls.append(("sql", statement)) or {"command": "COMMIT"}
        result = module.call_tool("postgres_admin_sql", {**args, "apply": True, "planHash": plan["planHash"]}, admin=True)
        self.assertEqual(result["status"], "executed")
        self.assertEqual(calls[0][1][3:5], ("supabase_admin", "test-only-password"))
        self.assertIn("BEGIN;", calls[2][1])
        self.assertIn("COMMIT;", calls[2][1])
        self.assertNotIn("test-only-password", json.dumps(result))
        self.assertEqual(requests[0][1], "/api/v1/namespaces/supabase-pg-re8ch/secrets/supabase-pg-re8ch-secrets")
        with self.assertRaisesRegex(PermissionError, "readOnly admin SQL"):
            module.postgres_admin_sql({"database": "postgres", "sql": sql})

    def test_external_profiles_do_not_inherit_qwen_tenant_defaults(self):
        for profile in ("observe", "operate", "admin"):
            module = load_profile(profile)
            self.assertEqual(module.TENANT, "platform")
            self.assertEqual(module.TENANT_NAMESPACES, [])

        legacy = load_profile("legacy")
        self.assertEqual(legacy.TENANT, "qwen")
        self.assertEqual(legacy.TENANT_NAMESPACES, ["qwen"])

    def test_admin_workflow_does_not_repeat_confirmation(self):
        workflow = load_profile("admin").get_tenancy_admin_workflow()
        safety = " ".join(workflow["safety"]).lower()
        self.assertNotIn("confirmation for every", safety)

    def test_platform_observe_exposes_only_fixed_flux_read_paths(self):
        module = load_profile("observe")
        self.assertEqual(
            module.resource_path("fluxgitrepositories"),
            "/apis/source.toolkit.fluxcd.io/v1/namespaces/flux-system/gitrepositories",
        )
        self.assertEqual(
            module.resource_path("fluxkustomizations", "flux-agent-system"),
            "/apis/kustomize.toolkit.fluxcd.io/v1/namespaces/flux-agent-system/kustomizations",
        )
        with self.assertRaisesRegex(ValueError, "unsupported Flux namespace"):
            module.resource_path("fluxkustomizations", "tenant-a")
        with self.assertRaises(ValueError):
            module.resource_path("flux-system/secrets")

    def test_platform_observe_can_aggregate_autonomous_flux_namespaces(self):
        module = load_profile("observe")
        requested = []

        def fake_request(_method, path, _body=None, _content_type="application/json"):
            requested.append(path)
            namespace = path.split("/namespaces/", 1)[1].split("/", 1)[0]
            return {"items": [{"kind": "Kustomization", "metadata": {"name": "domain", "namespace": namespace}}]}

        module.kube_request = fake_request
        result = module.list_resources("fluxkustomizations", "all")
        self.assertEqual(result["namespace"], "all")
        self.assertEqual(result["count"], 5)
        self.assertEqual({item["namespace"] for item in result["items"]}, set(module.FLUX_NAMESPACES))
        self.assertEqual(len(requested), 5)

        with self.assertRaisesRegex(ValueError, "only for fixed Flux"):
            module.list_resources("pods", "all")

    def test_metrics_summaries_expose_only_cpu_and_memory_usage(self):
        module = load_profile("observe")
        node = module.simplify_resource(
            {"kind": "NodeMetrics", "metadata": {"name": "restricted-node-1eb0570f74"}, "timestamp": "2026-09-20T00:00:00Z", "window": "15s", "usage": {"cpu": "123m", "memory": "456Mi", "ephemeral-storage": "1Gi"}}
        )
        self.assertEqual(node["usage"], {"cpu": "123m", "memory": "456Mi"})
        self.assertNotIn("ephemeral-storage", node["usage"])
        pod = module.simplify_resource(
            {"kind": "PodMetrics", "metadata": {"name": "scheduler", "namespace": "scheduler-system"}, "containers": [{"name": "scheduler", "usage": {"cpu": "50m", "memory": "96Mi", "ephemeral-storage": "1Gi"}}]}
        )
        self.assertEqual(pod["containers"], [{"name": "scheduler", "usage": {"cpu": "50m", "memory": "96Mi"}}])

    def test_agent_runtime_tolerates_reinitializing_sandbox_storage(self):
        module = load_profile("observe")

        def fake_request(_method, path, _body=None, _content_type="application/json"):
            if path.endswith("/pools"):
                raise RuntimeError("failed: 429 storage is (re)initializing")
            return {"items": []}

        module.kube_request = fake_request
        module.probe_http = lambda *args: {"name": args[0], "ready": False}
        result = module.get_agent_runtime_status()
        self.assertFalse(result["sandboxResources"]["pools"]["available"])
        self.assertTrue(result["sandboxResources"]["batchsandboxes"]["available"])


if __name__ == "__main__":
    unittest.main()
