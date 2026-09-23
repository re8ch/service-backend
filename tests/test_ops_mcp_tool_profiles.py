import importlib.util
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
            module.resource_path("fluxkustomizations"),
            "/apis/kustomize.toolkit.fluxcd.io/v1/namespaces/flux-system/kustomizations",
        )
        with self.assertRaises(ValueError):
            module.resource_path("flux-system/secrets")

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
