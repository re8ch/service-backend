import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "clusters/re8ch/components/ai-operations"


def load(monkeypatch):
    monkeypatch.setenv("RE8CH_TOOL_PROFILE", "tenant")
    monkeypatch.setenv("ALLOW_AUTHENTICATED_DEX", "true")
    sys.path.insert(0, str(COMPONENT))
    try:
        spec = importlib.util.spec_from_file_location("tenant_resource_mcp", COMPONENT / "ai_operations_mcp.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def test_tenant_profile_includes_claim_api(monkeypatch):
    module = load(monkeypatch)
    assert {
        "tenant_capabilities_list", "tenant_claim_plan", "tenant_claim_create",
        "tenant_claim_get", "tenant_claim_list", "tenant_claim_update",
        "tenant_claim_renew", "tenant_claim_cancel", "tenant_usage_summary_get",
    } <= {tool["name"] for tool in module.tools()}
    assert {"tenant_kubernetes_get", "tenant_kubernetes_apply", "tenant_kubernetes_delete"} <= {tool["name"] for tool in module.tools()}


def test_native_apply_forces_resolved_namespace(monkeypatch):
    module = load(monkeypatch)
    monkeypatch.setattr(module, "authenticated_tenant_context", lambda _identity: {"tenantRef": "acme", "role": "operator", "runtimeNamespaceRef": "tenant-acme"})
    value = module.call_tool("tenant_kubernetes_apply", {"object": {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "demo"}, "spec": {}}, "apply": False}, {"subject": "agent"})
    assert value["namespace"] == "tenant-acme"
    assert value["object"]["metadata"]["namespace"] == "tenant-acme"


def test_native_apply_rejects_cross_tenant_namespace(monkeypatch):
    module = load(monkeypatch)
    monkeypatch.setattr(module, "authenticated_tenant_context", lambda _identity: {"tenantRef": "acme", "role": "operator", "runtimeNamespaceRef": "tenant-acme"})
    import pytest
    with pytest.raises(PermissionError, match="outside"):
        module.call_tool("tenant_kubernetes_apply", {"object": {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "demo", "namespace": "other"}}, "apply": False}, {"subject": "agent"})


def test_native_delete_uses_supported_name_output(monkeypatch):
    module = load(monkeypatch)
    monkeypatch.setattr(module, "authenticated_tenant_context", lambda _identity: {"tenantRef": "acme", "role": "operator", "runtimeNamespaceRef": "tenant-acme"})
    calls = []
    monkeypatch.setattr(module, "command_result", lambda argv, stdin, timeout: calls.append(argv) or {"exit_code": 0, "stdout": "deployment.apps/demo", "stderr": ""})
    value = module.call_tool("tenant_kubernetes_delete", {"kind": "deployment", "name": "demo", "apply": True}, {"subject": "agent"})
    assert value["deleted"] is True
    assert calls == [["kubectl", "--namespace", "tenant-acme", "delete", "deployment", "demo", "-o", "name"]]


def test_capability_list_is_approval_free_and_unpriced(monkeypatch):
    module = load(monkeypatch)
    monkeypatch.setattr(module, "authenticated_tenant", lambda _identity: "acme")
    value = module.call_tool("tenant_capabilities_list", {}, {"subject": "agent"})
    assert {item["name"] for item in value["capabilities"]} == {"kubernetes.namespace.v1", "compute.workload.v1", "storage.volume.v1"}
    native = next(item for item in value["capabilities"] if item["name"] == "kubernetes.namespace.v1")
    assert native == {"name": "kubernetes.namespace.v1", "access": "native", "approvalRequired": False, "imagePolicy": "tenant-selected", "interfaces": ["Kubernetes API", "Helm", "GitOps"]}
    assert value["requiresApproval"] is False
    assert value["pricingIncluded"] is False
