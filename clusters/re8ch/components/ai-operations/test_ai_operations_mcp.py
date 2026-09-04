import importlib.util
import os
import tempfile
import unittest
import json
import sys
from pathlib import Path

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
        self.assertIn("harbor_tenant_robot_create", names)
        self.assertNotIn("harbor_users", names)
        self.assertNotIn("infra_execute", names)

    def test_tenant_infra_profile_exposes_service_lifecycle_only(self):
        m = load("tenant-infra")
        names = {tool["name"] for tool in m.tools()}
        self.assertTrue({"tenant_create", "tenant_grant_create", "tenant_visibility_update", "tenant_delete", "service_catalog_list", "service_instance_create", "service_binding_create", "usage_summary_get", "operation_get"}.issubset(names))
        self.assertNotIn("infra_execute", names)
        self.assertNotIn("harbor_users", names)
        self.assertNotIn("activate_device", names)
        with self.assertRaises(PermissionError):
            m.call_tool("operation_status", {"operation_id": "op_admin"}, {"tenant": "qwen", "groups": []})

    def test_tenant_planes_are_server_side_isolated_and_resources_are_tenant_scoped(self):
        registry = load("tenant-registry")
        registry.TENANT_SERVICE_DB = Path(tempfile.mkdtemp()) / "tenant.db"
        registry._TENANT_SERVICE_STORE = None
        names = {tool["name"] for tool in registry.tools()}
        self.assertIn("service_instance_create", names)
        self.assertNotIn("tenant_create", names)
        self.assertNotIn("byoc_cluster_register", names)
        identity = {"tenant": "canary", "groups": ["re8ch:tenant:canary"]}
        listed = registry.resources_list(identity, None)
        self.assertTrue(all("/canary/" in item["uri"] for item in listed["resources"]))
        catalog = registry.resources_read(identity, "re8ch://tenant/canary/catalog")
        body = json.loads(catalog["contents"][0]["text"])
        self.assertEqual({item["serviceClass"] for item in body["services"]}, {"registry.harbor.v1"})
        with self.assertRaises(PermissionError):
            registry.resources_read(identity, "re8ch://tenant/other/instances")

    def test_byoc_registration_returns_opaque_reference_and_hashes_key(self):
        byoc = load("tenant-byoc")
        byoc.TENANT_SERVICE_DB = Path(tempfile.mkdtemp()) / "tenant.db"
        byoc._TENANT_SERVICE_STORE = None
        identity = {"tenant": "canary", "groups": ["re8ch:tenant:canary"]}
        result = byoc.call_tool("byoc_cluster_register", {"cluster_uid": "cluster-uid-123", "region_ref": "cn-east", "issuer": "https://issuer.example", "public_key": "public-key", "capabilities": ["mcs"]}, identity)
        self.assertTrue(result["enrollmentRef"].startswith("enrollment://"))
        stored = byoc.tenant_service_store().registrations("canary")[0]
        self.assertNotEqual(stored["public_key_hash"], "public-key")
        self.assertTrue(stored["public_key_hash"].startswith("sha256:"))

    def test_tenant_context_is_derived_from_exactly_one_declared_group(self):
        m = load("tenant-infra")
        self.assertEqual(m.authenticated_tenant({"groups": ["re8ch:tenant:research-a"]}), "research-a")
        self.assertEqual(m.authenticated_tenant({"groups": ["re8ch:tenant-admins"]}), "admin")
        with self.assertRaises(PermissionError):
            m.authenticated_tenant({"tenant": "admin", "groups": []})
        with self.assertRaises(PermissionError):
            m.authenticated_tenant({"groups": ["re8ch:qwen-operators", "re8ch:ledger-operators"]})

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
