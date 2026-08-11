import importlib.util
import os
from pathlib import Path
import unittest


APP = Path(__file__).parents[1] / "manifests/k3s/ops/ops-mcp-server-app.py"


def load_profile(profile):
    os.environ["OPS_TOOL_PROFILE"] = profile
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

    def test_each_profile_advertises_minimum_oauth_scope(self):
        for profile, scope in (
            ("observe", "platform:observe"),
            ("operate", "platform:operate"),
            ("admin", "platform:admin"),
        ):
            for tool in self.tools(profile).values():
                self.assertEqual(
                    tool["securitySchemes"],
                    [{"type": "oauth2", "scopes": [scope]}],
                    tool["name"],
                )

    def test_operate_write_schema_has_no_approval_id(self):
        tools = self.tools("operate")
        for name in ("restart_managed_platform_deployment", "scale_managed_platform_deployment"):
            self.assertFalse(tools[name]["annotations"]["readOnlyHint"])
            self.assertNotIn("approvalId", tools[name]["inputSchema"]["properties"])
        replicas = tools["scale_managed_platform_deployment"]["inputSchema"]["properties"]["replicas"]
        self.assertEqual(replicas["enum"], [0, 1])

    def test_admin_excludes_legacy_dex_and_secret_values(self):
        admin = set(self.tools("admin"))
        self.assertIn("list_authentik_users", admin)
        self.assertIn("grant_authentik_user_group", admin)
        self.assertFalse(any("dex" in name for name in admin))
        self.assertNotIn("get_kubeconfig", admin)
        self.assertNotIn("postgres_sql_proxy", admin)


if __name__ == "__main__":
    unittest.main()
