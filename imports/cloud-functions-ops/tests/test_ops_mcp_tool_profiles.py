import importlib.util
import os
from pathlib import Path
import unittest


APP = Path(__file__).parents[1] / "manifests/k3s/ops/ops-mcp-server-app.py"


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
            {"get_managed_platform_operation_status", "restart_managed_platform_deployment", "scale_managed_platform_deployment"},
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


if __name__ == "__main__":
    unittest.main()
