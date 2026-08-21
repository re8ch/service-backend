import importlib.util
import os
import tempfile
import unittest
import json
from pathlib import Path


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

    def test_litellm_uses_its_real_health_path(self):
        m = load("orchestra")
        self.assertEqual(m.ORCHESTRA_HEALTH_PATHS["litellm"], "/health/liveliness")

    def test_cross_audience_token_rejected(self):
        cloud = load("cloud")
        _, token = cloud.registry().activate("user-1", [cloud.ADMIN_GROUP], None)
        infra = load("infra")
        self.assertIsNone(infra.registry().authenticate(token))

    def test_capsule_is_signed_one_time_and_broker_hides_material(self):
        m = load("cloud")
        identity = {"subject": "user-1", "groups": [], "device_id": "dev-test", "auth_type": "device"}
        signed = m.create_capsule(identity, {"action": "shell", "node": "restricted-node-6dc3f140f9", "command": "hostname", "credential_refs": ["node-ssh"]})
        m.command_result = lambda argv, stdin, timeout: {"operation_id": "op-test", "exit_code": 0, "stdout": "restricted-node-6dc3f140f9\n", "stderr": ""}
        result = m.execute_capsule(identity, signed["capsule"], signed["signature"])
        self.assertEqual(result["exit_code"], 0)
        self.assertFalse(result["credential_material_exposed"])
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


if __name__ == "__main__":
    unittest.main()
