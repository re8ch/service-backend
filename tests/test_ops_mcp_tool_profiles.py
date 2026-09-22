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
