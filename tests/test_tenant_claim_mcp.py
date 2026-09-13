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


def test_capability_list_is_approval_free_and_unpriced(monkeypatch):
    module = load(monkeypatch)
    monkeypatch.setattr(module, "authenticated_tenant", lambda _identity: "acme")
    value = module.call_tool("tenant_capabilities_list", {}, {"subject": "agent"})
    assert {item["name"] for item in value["capabilities"]} == {"kubernetes.namespace.v1", "compute.workload.v1", "storage.volume.v1"}
    native = next(item for item in value["capabilities"] if item["name"] == "kubernetes.namespace.v1")
    assert native == {"name": "kubernetes.namespace.v1", "access": "native", "approvalRequired": False, "imagePolicy": "tenant-selected", "interfaces": ["Kubernetes API", "Helm", "GitOps"]}
    assert value["requiresApproval"] is False
    assert value["pricingIncluded"] is False
