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


def allow_ready_namespace(monkeypatch, module, tenant="acme", namespace="tenant-acme"):
    monkeypatch.setattr(module, "tenant_control_request", lambda *_args, **_kwargs: {"items": [
        {"spec": {"tenantRef": tenant, "namespaceName": namespace, "operation": "EnsurePresent"}, "status": {"phase": "Ready"}}
    ]})


def test_tenant_profile_includes_claim_api(monkeypatch):
    module = load(monkeypatch)
    assert {
        "tenant_capabilities_list", "tenant_claim_plan", "tenant_claim_create",
        "tenant_claim_get", "tenant_claim_list", "tenant_claim_update",
        "tenant_claim_renew", "tenant_claim_cancel", "tenant_usage_summary_get",
    } <= {tool["name"] for tool in module.tools()}
    assert {"tenant_kubernetes_get", "tenant_kubernetes_apply", "tenant_kubernetes_delete"} <= {tool["name"] for tool in module.tools()}


def test_human_credential_requires_ready_matching_instance(monkeypatch):
    module = load(monkeypatch)
    monkeypatch.setattr(module, "tenant_control_request", lambda *_args: {"items": [{"spec": {"serviceClass": "registry.harbor.v1", "lifecycle": "Requested"}, "status": {"phase": "Ready", "humanCredentialRef": "credential://harbor/acme/" + "a" * 32}}]})
    module.require_ready_human_credential("acme", "credential://harbor/acme/" + "a" * 32)
    import pytest
    with pytest.raises(PermissionError, match="ready"):
        module.require_ready_human_credential("acme", "credential://harbor/acme/" + "b" * 32)
    with pytest.raises(PermissionError, match="cross-tenant"):
        module.require_ready_human_credential("other", "credential://harbor/acme/" + "a" * 32)


def test_native_apply_forces_resolved_namespace(monkeypatch):
    module = load(monkeypatch)
    monkeypatch.setattr(module, "authenticated_tenant_context", lambda _identity: {"tenantRef": "acme", "role": "operator", "runtimeNamespaceRef": "tenant-acme"})
    allow_ready_namespace(monkeypatch, module)
    value = module.call_tool("tenant_kubernetes_apply", {"object": {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "demo"}, "spec": {}}, "apply": False}, {"subject": "agent"})
    assert value["namespace"] == "tenant-acme"
    assert value["object"]["metadata"]["namespace"] == "tenant-acme"
    labels = value["object"]["spec"]["template"]["metadata"]["labels"]
    assert labels["tenant"] == "acme"
    assert labels["birfucate.re8ch.com/metered"] == "true"
    assert labels["resource_ref"] == value["object"]["metadata"]["labels"]["resource_ref"]


def test_native_pvc_is_metered_without_claim_ownership(monkeypatch):
    module = load(monkeypatch)
    monkeypatch.setattr(module, "authenticated_tenant_context", lambda _identity: {"tenantRef": "acme", "role": "operator", "runtimeNamespaceRef": "tenant-acme"})
    allow_ready_namespace(monkeypatch, module)
    obj = {"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": {"name": "data"}, "spec": {"resources": {"requests": {"storage": "1Gi"}}}}
    value = module.call_tool("tenant_kubernetes_apply", {"object": obj, "apply": False}, {"subject": "agent"})
    assert value["object"]["metadata"]["labels"]["birfucate.re8ch.com/metered"] == "true"
    assert "labels" not in obj["metadata"]


def test_claim_usage_summary_reports_accepted_events(monkeypatch, tmp_path):
    module = load(monkeypatch)
    module.TENANT_SERVICE_DB = tmp_path / "usage.db"
    module._TENANT_SERVICE_STORE = None
    monkeypatch.setattr(module, "tenant_control_request", lambda *_args, **_kwargs: {"items": [{
        "metadata": {"name": "data", "uid": "claim-uid-1"},
        "spec": {"capability": "storage.volume.v1", "parameters": {"sizeGiB": 1}},
        "status": {"phase": "Ready"},
    }]})
    event = {"eventId": "ue_" + "d" * 32, "tenantRef": "re8ch", "resourceRef": "claim-uid-1",
             "serviceClass": "storage.volume.v1", "meter": "occupancy.ceph-volume-capacity", "quantity": 60,
             "unit": "GiB-second", "periodStart": "2026-09-01T00:00:00Z", "periodEnd": "2026-09-01T00:01:00Z",
             "regionRef": "loudi", "sourceOperationId": "birfucate:test", "observedAt": "2026-09-01T00:01:00Z",
             "schemaVersion": "usage.re8ch.com/v1", "pricingApplied": False, "accountingPosted": False}
    module.tenant_service_store().receive_usage(event)
    result = module.tenant_claim_usage_summary("re8ch")
    assert result["claims"][0]["requested"] == {"storageGiB": 1}
    assert result["claims"][0]["usage"]["totals"] == {"occupancy.ceph-volume-capacity:GiB-second": 60.0}
    assert result["usageState"] == "accepted-events"


def test_native_apply_rejects_cross_tenant_namespace(monkeypatch):
    module = load(monkeypatch)
    monkeypatch.setattr(module, "authenticated_tenant_context", lambda _identity: {"tenantRef": "acme", "role": "operator", "runtimeNamespaceRef": "tenant-acme"})
    allow_ready_namespace(monkeypatch, module)
    import pytest
    with pytest.raises(PermissionError, match="outside"):
        module.call_tool("tenant_kubernetes_apply", {"object": {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "demo", "namespace": "other"}}, "apply": False}, {"subject": "agent"})


def test_native_delete_uses_supported_name_output(monkeypatch):
    module = load(monkeypatch)
    monkeypatch.setattr(module, "authenticated_tenant_context", lambda _identity: {"tenantRef": "acme", "role": "operator", "runtimeNamespaceRef": "tenant-acme"})
    allow_ready_namespace(monkeypatch, module)
    calls = []
    monkeypatch.setattr(module, "command_result", lambda argv, stdin, timeout: calls.append(argv) or {"exit_code": 0, "stdout": "deployment.apps/demo", "stderr": ""})
    value = module.call_tool("tenant_kubernetes_delete", {"kind": "deployment", "name": "demo", "apply": True}, {"subject": "agent"})
    assert value["deleted"] is True
    assert calls == [["kubectl", "--namespace", "tenant-acme", "delete", "deployment", "demo", "-o", "name"]]


def test_native_delete_rejects_read_only_kinds_and_foreign_namespace(monkeypatch):
    import pytest

    module = load(monkeypatch)
    monkeypatch.setattr(module, "authenticated_tenant_context", lambda _identity: {"tenantRef": "acme", "role": "operator", "runtimeNamespaceRef": "tenant-acme"})
    calls = []
    monkeypatch.setattr(module, "command_result", lambda *args: calls.append(args) or {"exit_code": 0})
    allow_ready_namespace(monkeypatch, module)
    for kind in ("pod", "event", "endpoint", "replicaset"):
        with pytest.raises(PermissionError, match="workload allowlist"):
            module.call_tool("tenant_kubernetes_delete", {"kind": kind, "name": "demo", "apply": True}, {"subject": "agent"})
    allow_ready_namespace(monkeypatch, module, tenant="other")
    with pytest.raises(PermissionError, match="Ready namespace"):
        module.call_tool("tenant_kubernetes_delete", {"kind": "deployment", "name": "demo", "apply": True}, {"subject": "agent"})
    assert calls == []


def test_database_credential_discovery_returns_only_tenant_metadata(monkeypatch):
    import pytest

    module = load(monkeypatch)
    monkeypatch.setattr(module, "authenticated_tenant_context", lambda _identity: {"tenantRef": "re8ch", "role": "operator", "defaultNamespace": "re8ch-default"})
    monkeypatch.setattr(module, "tenant_control_request", lambda *_args, **_kwargs: {"items": [
        {"spec": {"tenantRef": "re8ch", "namespaceName": "re8ch-default", "operation": "EnsurePresent"}, "status": {"phase": "Ready"}}
    ]})
    grants = [{"consumableRef": "database.postgresql.shared.v1", "canRequest": True}]
    monkeypatch.setattr(module, "authenticated_tenant_grants", lambda _identity: grants)
    calls = []
    def command(argv, stdin, timeout):
        calls.append(argv)
        return {"exit_code": 0, "stdout": "pg-ledger-credentials\tuid-123\tledger\tPGDATABASE,DATABASE_URL_RW,\n", "stderr": ""}
    monkeypatch.setattr(module, "command_result", command)
    value = module.call_tool("tenant_kubernetes_get", {"kind": "databasecredential"}, {"subject": "agent"})
    assert value["items"] == [{"database": "ledger", "secretRef": "re8ch-default/pg-ledger-credentials", "secretUID": "uid-123", "keys": ["DATABASE_URL_RW", "PGDATABASE"], "databaseState": "unverified"}]
    assert "-l" in calls[0] and "re8ch.com/tenant=re8ch-default" in calls[0][calls[0].index("-l") + 1]
    assert ".data" in module.DATABASE_BINDING_TEMPLATE and "{{$value}}" not in module.DATABASE_BINDING_TEMPLATE
    grants.clear()
    with pytest.raises(PermissionError, match="active tenant grant"):
        module.call_tool("tenant_kubernetes_get", {"kind": "databasecredential"}, {"subject": "agent"})
    assert len(calls) == 1


def test_capability_list_is_approval_free_and_unpriced(monkeypatch):
    module = load(monkeypatch)
    monkeypatch.setattr(module, "authenticated_tenant", lambda _identity: "acme")
    monkeypatch.setattr(module, "authenticated_tenant_grants", lambda _identity: [])
    value = module.call_tool("tenant_capabilities_list", {}, {"subject": "agent"})
    assert {item["name"] for item in value["capabilities"]} == {"kubernetes.namespace.v1", "compute.workload.v1", "storage.volume.v1"}
    native = next(item for item in value["capabilities"] if item["name"] == "kubernetes.namespace.v1")
    assert native == {"name": "kubernetes.namespace.v1", "access": "native", "approvalRequired": False, "imagePolicy": "tenant-selected", "interfaces": ["Kubernetes API", "Helm", "GitOps"]}
    assert value["requiresApproval"] is False
    assert value["pricingIncluded"] is False


def test_amongclusters_service_requests_use_grant_enforcing_controller(monkeypatch):
    module = load(monkeypatch)
    monkeypatch.setattr(module, "authenticated_tenant", lambda _identity: "artchais")
    monkeypatch.setattr(module, "authenticated_tenant_context", lambda _identity: {"tenantRef": "artchais", "role": "operator"})
    monkeypatch.setattr(module, "authenticated_tenant_grants", lambda _identity: [{"consumableRef": "network.among-clusters.v1", "canRequest": True}])
    monkeypatch.setattr(module, "tenant_service_store", lambda: object())
    calls = []
    monkeypatch.setattr(module, "tenant_control_request", lambda *args, **kwargs: calls.append((args, kwargs)) or {"decision": "preview"})
    spec = {"serviceClass": "network.among-clusters.v1", "planRef": "private", "regionRef": "global", "parameters": {"sourceClusterRef": "artc", "destinationClusterRefs": ["re8ch"], "serviceRefs": ["network.egress.mihomo"]}}
    assert module.call_tool("service_instance_plan", {"name": "mihomos", "spec": spec}, {"subject": "agent"}) == {"decision": "preview"}
    assert calls[0][0][0] == "/v1/services/plan"
    module.call_tool("service_instance_create", {"name": "mihomos", "spec": spec, "idempotency_key": "create-001"}, {"subject": "agent"})
    assert calls[1][0][0] == "/v1/services"
    assert calls[1][0][2]["idempotencyKey"] == "create-001"


def test_harbor_and_grafana_service_requests_use_controller_not_local_store(monkeypatch):
    module = load(monkeypatch)
    monkeypatch.setattr(module, "authenticated_tenant", lambda _identity: "acme")
    monkeypatch.setattr(module, "authenticated_tenant_context", lambda _identity: {"tenantRef": "acme", "role": "operator"})
    monkeypatch.setattr(module, "authenticated_tenant_grants", lambda _identity: [{"consumableRef": ref, "canRequest": True} for ref in ("registry.harbor.v1", "observability.grafana.v1")])
    class NoLocalStore:
        def plan(self, *_args): raise AssertionError("local intent store must not plan provider services")
        def mutate(self, *_args): raise AssertionError("local intent store must not create provider services")
    monkeypatch.setattr(module, "tenant_service_store", NoLocalStore)
    calls = []
    monkeypatch.setattr(module, "tenant_control_request", lambda *args, **kwargs: calls.append(args) or {"decision": "preview"})
    for service_class in ("registry.harbor.v1", "observability.grafana.v1"):
        spec = {"serviceClass": service_class, "planRef": "pull" if service_class.startswith("registry") else "viewer", "parameters": {}}
        module.call_tool("service_instance_plan", {"name": "access", "spec": spec}, {"subject": "agent"})
        module.call_tool("service_instance_create", {"name": "access", "spec": spec, "idempotency_key": "create-12345"}, {"subject": "agent"})
    assert [call[0] for call in calls] == ["/v1/services/plan", "/v1/services", "/v1/services/plan", "/v1/services"]


def test_artchais_catalog_hides_amongclusters_when_controller_denies_product(monkeypatch):
    module = load(monkeypatch)
    monkeypatch.setattr(module, "authenticated_tenant", lambda _identity: "artchais")
    monkeypatch.setattr(module, "authenticated_tenant_grants", lambda _identity: [{"consumableRef": "network.among-clusters.v1", "lifecycle": "Active"}])
    monkeypatch.setattr(module, "tenant_service_store", lambda: object())
    def deny_product(*_args, **_kwargs): raise PermissionError("product unavailable")
    monkeypatch.setattr(module, "tenant_control_request", deny_product)
    value = module.call_tool("service_catalog_list", {}, {"subject": "agent"})
    assert "network.among-clusters.v1" not in {item["serviceClass"] for item in value["services"]}


def test_service_catalog_excludes_intent_only_classes_and_rejects_fake_provisioning(monkeypatch):
    module = load(monkeypatch)
    calls = []
    class Store:
        def plan(self, *args): calls.append(("plan", args)); return {"decision": "preview"}
        def mutate(self, *args): calls.append(("mutate", args)); return {"state": "Provisioning"}
        def get(self, *_args): return {"name": "disk", "service_class": "storage.volume.v1", "spec": {"serviceClass": "storage.volume.v1"}}
        def create_binding(self, *args): calls.append(("bind", args)); return {"bindingRef": "binding://acme/one"}
    monkeypatch.setattr(module, "tenant_service_store", Store)
    monkeypatch.setattr(module, "authenticated_tenant", lambda _identity: "acme")
    monkeypatch.setattr(module, "authenticated_tenant_context", lambda _identity: {"tenantRef": "acme", "role": "operator"})
    monkeypatch.setattr(module, "authenticated_tenant_grants", lambda _identity: [{"consumableRef": "storage.volume.v1", "canRequest": True}])
    def no_provider_instance(*_args, **_kwargs): raise ValueError("local service")
    monkeypatch.setattr(module, "tenant_control_request", no_provider_instance)
    import pytest
    spec = {"serviceClass": "storage.volume.v1", "planRef": "shared", "regionRef": "loudi", "parameters": {"sizeGiB": 1}}
    listed = module.call_tool("service_catalog_list", {}, {"subject": "agent"})
    assert "storage.volume.v1" not in {item["serviceClass"] for item in listed["services"]}
    for name, arguments in [
        ("service_instance_plan", {"name": "disk", "spec": spec}),
        ("service_instance_create", {"name": "disk", "spec": spec, "idempotency_key": "key-12345"}),
        ("service_binding_create", {"resource_ref": "service://acme/disk", "binding_type": "access", "parameters": {}}),
        ("service_instance_delete", {"resource_ref": "service://acme/disk", "idempotency_key": "delete-12345"}),
    ]:
        with pytest.raises(module.ServiceError, match="tenant_claim_plan and tenant_claim_create"):
            module.call_tool(name, arguments, {"subject": "agent"})
    assert calls == []


def test_namespace_tools_use_authenticated_tenant_and_explicit_prefix(monkeypatch):
    module = load(monkeypatch)
    monkeypatch.setattr(module, "authenticated_tenant_context", lambda _identity: {"tenantRef": "re8ch", "role": "operator", "defaultNamespace": "re8ch-default", "runtimeNamespaceRef": "re8ch-default"})
    calls = []
    monkeypatch.setattr(module, "tenant_control_request", lambda path, tenant, payload=None, method=None, caller="tenant": calls.append((path, tenant, payload, method)) or {"status": "accepted"})
    value = module.call_tool("tenant_namespace_create", {"namespace": "re8ch-demo"}, {"subject": "agent"})
    assert value == {"status": "accepted"}
    assert calls == [("/v1/namespaces", "re8ch", {"namespaceName": "re8ch-demo"}, None)]


def test_tenant_namespace_ttl_is_forwarded_and_expired_namespace_is_denied(monkeypatch):
    module = load(monkeypatch)
    monkeypatch.setattr(module, "authenticated_tenant_context", lambda _identity: {"tenantRef": "re8ch", "role": "operator", "defaultNamespace": "re8ch-default", "runtimeNamespaceRef": "re8ch-default"})
    calls = []
    def request(path, tenant, payload=None, method=None, caller="tenant"):
        calls.append((path, tenant, payload))
        if payload:
            return {"status": "accepted"}
        return {"items": [{"spec": {"tenantRef": "re8ch", "namespaceName": "re8ch-test-demo-pr-1", "operation": "EnsurePresent", "expiresAt": "2020-01-01T00:00:00Z"}, "status": {"phase": "Ready"}}]}
    monkeypatch.setattr(module, "tenant_control_request", request)
    module.call_tool("tenant_namespace_create", {"namespace": "re8ch-test-demo-pr-1", "ttlSeconds": 3600}, {"subject": "agent"})
    assert calls[0] == ("/v1/namespaces", "re8ch", {"namespaceName": "re8ch-test-demo-pr-1", "ttlSeconds": 3600})
    with __import__("pytest").raises(PermissionError, match="Ready namespace"):
        module.call_tool("tenant_kubernetes_apply", {"namespace": "re8ch-test-demo-pr-1", "object": {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "demo"}}, "apply": False}, {"subject": "agent"})


def test_explicit_workload_namespace_must_be_ready_and_owned(monkeypatch):
    module = load(monkeypatch)
    monkeypatch.setattr(module, "authenticated_tenant_context", lambda _identity: {"tenantRef": "re8ch", "role": "operator", "defaultNamespace": "re8ch-default", "runtimeNamespaceRef": "re8ch-default"})
    monkeypatch.setattr(module, "tenant_control_request", lambda *_args, **_kwargs: {"items": [{"spec": {"tenantRef": "re8ch", "namespaceName": "re8ch-demo", "operation": "EnsurePresent"}, "status": {"phase": "Ready"}}]})
    value = module.call_tool("tenant_kubernetes_apply", {"namespace": "re8ch-demo", "object": {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "demo"}}, "apply": False}, {"subject": "agent"})
    assert value["namespace"] == "re8ch-demo"
    with __import__("pytest").raises(PermissionError, match="Ready namespace"):
        module.call_tool("tenant_kubernetes_apply", {"namespace": "re8ch-other", "object": {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "demo"}}, "apply": False}, {"subject": "agent"})


def test_native_workload_security_boundaries(monkeypatch):
    module = load(monkeypatch)
    base = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "demo"}, "spec": {"template": {"spec": {"containers": [{"name": "demo", "image": "nginx", "securityContext": {"privileged": True}}]}}}}
    with __import__("pytest").raises(PermissionError, match="privileged"):
        module.validate_tenant_kubernetes_object(base, "re8ch-default")
    external_ip = {"kind": "Service", "spec": {"type": "ClusterIP", "externalIPs": ["203.0.113.10"]}}
    with __import__("pytest").raises(PermissionError, match="externalIPs"):
        module.validate_tenant_kubernetes_object(external_ip, "re8ch-default")
    host_port = {"kind": "Deployment", "spec": {"template": {"spec": {"containers": [{"name": "demo", "ports": [{"containerPort": 8080, "hostPort": 8080}]}]}}}}
    with __import__("pytest").raises(PermissionError, match="hostPort"):
        module.validate_tenant_kubernetes_object(host_port, "re8ch-default")
    service = {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "public"}, "spec": {"type": "NodePort"}}
    with __import__("pytest").raises(PermissionError, match="PublicEdge"):
        module.validate_tenant_kubernetes_object(service, "re8ch-default")
