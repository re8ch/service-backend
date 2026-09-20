import importlib.util
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "tenant_control_controller",
    ROOT / "clusters/re8ch/components/ai-operations/tenant_control_controller.py",
)
controller = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(controller)


class FakeKube:
    def __init__(self, bindings, tenants):
        self.values = {"tenantidentitybindings": bindings, "tenants": tenants}

    def list(self, plural):
        return self.values[plural]


def binding(tenant, digest="sha256:" + "a" * 64, revoked=False, role="viewer"):
    return {"spec": {"tenantRef": tenant, "issuer": "https://dex.re8ch.com", "subjectHash": digest, "role": role, "revoked": revoked}}


def group_binding(tenant, group, role):
    return {"spec": {"tenantRef": tenant, "issuer": "https://dex.re8ch.com", "group": group, "role": role}}


def tenant(name, lifecycle="Active"):
    return {"metadata": {"name": name}, "spec": {"lifecycle": lifecycle}}


def test_identity_resolves_only_one_active_tenant(monkeypatch):
    monkeypatch.setattr(controller, "KUBE", FakeKube([binding("acme")], [tenant("acme")]))
    assert controller.resolve("https://dex.re8ch.com/", "sha256:" + "a" * 64) == {"tenantRef": "acme", "role": "viewer", "defaultNamespace": "acme", "runtimeNamespaceRef": "acme"}


def test_group_identity_resolves_highest_role_for_one_tenant(monkeypatch):
    bindings = [
        group_binding("artchais", "Artchais:tenant-viewers", "viewer"),
        group_binding("artchais", "Artchais:tenant-operators", "operator"),
    ]
    monkeypatch.setattr(controller, "KUBE", FakeKube(bindings, [tenant("artchais")]))
    result = controller.resolve("https://dex.re8ch.com", "", ["Artchais:tenant-viewers", "Artchais:tenant-operators"])
    assert result == {"tenantRef": "artchais", "role": "operator", "defaultNamespace": "artchais", "runtimeNamespaceRef": "artchais"}


def test_tenant_publication_excludes_admin_binding(monkeypatch):
    bindings = [
        group_binding("admin", "re8ch:tenant-admins", "admin"),
        group_binding("artchais", "Artchais:tenant-operators", "operator"),
    ]
    monkeypatch.setattr(controller, "KUBE", FakeKube(bindings, [tenant("admin"), tenant("artchais")]))
    groups = ["re8ch:tenant-admins", "Artchais:tenant-operators"]
    assert controller.resolve("https://dex.re8ch.com", "", groups, caller="tenant")["tenantRef"] == "artchais"
    assert controller.resolve("https://dex.re8ch.com", "", groups, caller="admin")["tenantRef"] == "admin"


def test_admin_identity_projects_to_re8ch_operator_in_tenant_publication(monkeypatch):
    bindings = [
        group_binding("admin", "re8ch:tenant-admins", "admin"),
        group_binding("re8ch", "re8ch:tenant-admins", "operator"),
    ]
    monkeypatch.setattr(
        controller, "KUBE", FakeKube(bindings, [tenant("admin"), tenant("re8ch")])
    )
    groups = ["re8ch:tenant-admins"]
    assert controller.resolve("https://dex.re8ch.com", "", groups, caller="tenant") == {
        "tenantRef": "re8ch",
        "role": "operator",
        "defaultNamespace": "re8ch",
        "runtimeNamespaceRef": "re8ch",
    }
    assert controller.resolve("https://dex.re8ch.com", "", groups, caller="admin") == {
        "tenantRef": "admin",
        "role": "admin",
        "defaultNamespace": "admin",
        "runtimeNamespaceRef": "admin",
    }


def test_namespace_policy_rejects_foreign_and_reserved_names(monkeypatch):
    value = tenant("re8ch")
    value["spec"]["namespacePolicy"] = {"defaultNamespace": "re8ch-default", "allowedPrefixes": ["re8ch-"], "maxNamespaces": 20, "allowCreate": True, "allowDelete": True}
    monkeypatch.setattr(controller, "KUBE", FakeKube([], [value]))
    assert controller.validate_tenant_namespace("re8ch", "re8ch-demo", "EnsurePresent")["maxNamespaces"] == 20
    with pytest.raises(PermissionError, match="outside"):
        controller.validate_tenant_namespace("re8ch", "ledger", "EnsurePresent")
    with pytest.raises(PermissionError, match="outside"):
        controller.validate_tenant_namespace("re8ch", "artchais-demo", "EnsurePresent")


def test_namespace_resources_bind_only_namespaced_tenant_authority(monkeypatch):
    value = tenant("re8ch")
    value["metadata"]["uid"] = "tenant-uid"
    monkeypatch.setattr(controller, "KUBE", FakeKube([group_binding("re8ch", "re8ch:tenant-admins", "operator")], [value]))
    resources = controller.tenant_namespace_resources(value, "re8ch-demo")
    namespace = resources[0]
    assert namespace["metadata"]["annotations"]["tenancy.re8ch.com/tenant-uid"] == "tenant-uid"
    assert namespace["metadata"]["labels"]["tenancy.re8ch.com/tenant"] == "re8ch"
    assert namespace["metadata"]["labels"]["tenancy.re8ch.com/managed"] == "true"
    role = next(item for item in resources if item["kind"] == "Role")
    assert "namespaces" not in str(role["rules"])
    assert "clusterroles" not in str(role["rules"])


def test_groups_spanning_tenants_are_denied(monkeypatch):
    bindings = [group_binding("artchais", "Artchais:tenant-admins", "admin"), group_binding("other", "Other:tenant-admins", "admin")]
    monkeypatch.setattr(controller, "KUBE", FakeKube(bindings, [tenant("artchais"), tenant("other")]))
    with pytest.raises(PermissionError):
        controller.resolve("https://dex.re8ch.com", "", ["Artchais:tenant-admins", "Other:tenant-admins"])


def test_explicit_subject_binding_overrides_group_membership_in_another_tenant(monkeypatch):
    digest = "sha256:" + "a" * 64
    bindings = [
        binding("re8ch", digest=digest, role="operator"),
        group_binding("artchais", "Artchais", "viewer"),
    ]
    monkeypatch.setattr(
        controller,
        "KUBE",
        FakeKube(bindings, [tenant("re8ch"), tenant("artchais")]),
    )

    assert controller.resolve(
        "https://dex.re8ch.com", digest, ["Artchais"]
    ) == {
        "tenantRef": "re8ch",
        "role": "operator",
        "defaultNamespace": "re8ch",
        "runtimeNamespaceRef": "re8ch",
    }


def test_controller_exposes_only_admin_and_tenant_callers():
    assert controller.ALLOWED_CALLERS == {"admin", "tenant"}


def test_lease_creation_uses_kubernetes_microtime(monkeypatch):
    client = controller.Kubernetes.__new__(controller.Kubernetes)
    requests = []

    def request(method, path, body=None, content_type="application/json"):
        requests.append((method, path, body))
        if method == "GET":
            raise controller.urllib.error.HTTPError(path, 404, "not found", {}, None)
        return {}

    monkeypatch.setattr(client, "request", request)
    assert client.claim_lease() is True
    body = requests[-1][2]
    microtime = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
    assert microtime.fullmatch(body["spec"]["acquireTime"])
    assert microtime.fullmatch(body["spec"]["renewTime"])


def test_lease_renewal_sends_a_complete_replace_document(monkeypatch):
    client = controller.Kubernetes.__new__(controller.Kubernetes)
    requests = []

    def request(method, path, body=None, content_type="application/json"):
        requests.append((method, path, body))
        if method == "GET":
            return {
                "apiVersion": "coordination.k8s.io/v1",
                "kind": "Lease",
                "metadata": {"name": controller.LEASE_NAME, "namespace": controller.NAMESPACE, "resourceVersion": "42"},
                "spec": {"holderIdentity": controller.IDENTITY, "acquireTime": "2026-09-13T00:00:00.000000Z", "renewTime": "2026-09-13T00:00:00.000000Z"},
            }
        return {}

    monkeypatch.setattr(client, "request", request)
    assert client.claim_lease() is True
    method, _, body = requests[-1]
    assert method == "PUT"
    assert body["apiVersion"] == "coordination.k8s.io/v1"
    assert body["kind"] == "Lease"
    assert body["metadata"] == {"name": controller.LEASE_NAME, "namespace": controller.NAMESPACE, "resourceVersion": "42"}
    assert body["spec"]["acquireTime"] == "2026-09-13T00:00:00.000000Z"


@pytest.mark.parametrize(
    "bindings,tenants",
    [
        ([], [tenant("acme")]),
        ([binding("acme"), binding("other")], [tenant("acme"), tenant("other")]),
        ([binding("acme", revoked=True)], [tenant("acme")]),
        ([binding("acme")], [tenant("acme", "Suspended")]),
    ],
)
def test_missing_ambiguous_revoked_or_inactive_identity_is_denied(monkeypatch, bindings, tenants):
    monkeypatch.setattr(controller, "KUBE", FakeKube(bindings, tenants))
    with pytest.raises(PermissionError):
        controller.resolve("https://dex.re8ch.com", "sha256:" + "a" * 64)


def test_bare_artchais_organization_enrolls_without_a_role_team(monkeypatch):
    import yaml
    docs = list(yaml.safe_load_all((ROOT / "platform/birfucate-metering/release.yaml").read_text()))
    release = next(item for item in docs if item["kind"] == "HelmRelease")
    tenants = release["spec"]["values"]["tenantPlatform"]["tenants"]
    artchais = next(item for item in tenants if item["name"] == "artchais")
    bindings = [
        group_binding("artchais", item["group"], item["role"])
        for item in artchais["identityBindings"]
        if "group" in item
    ]
    monkeypatch.setattr(controller, "KUBE", FakeKube(bindings, [tenant("artchais")]))
    assert controller.resolve("https://dex.re8ch.com", "", ["Artchais"])["role"] == "viewer"
    assert controller.resolve("https://dex.re8ch.com", "", ["Artchais", "Artchais:tenant-admins"])["role"] == "admin"
    with pytest.raises(PermissionError):
        controller.resolve("https://dex.re8ch.com", "", ["OtherOrganization"])
    with pytest.raises(PermissionError):
        controller.resolve("https://dex.re8ch.com", "", ["Artchais"], caller="admin")


class AmongClustersKube:
    def list(self, plural, namespace=None, api=None):
        if plural == "tenantgrants":
            return [{"spec": {"tenantRef": "artchais", "consumableRef": "network.among-clusters.v1", "lifecycle": "Active", "roleRef": "operator"}}]
        if plural == "peers":
            return [{"metadata": {"name": "re8ch-artc"}, "spec": {"remoteIdentityRef": "artc", "suspended": False}}]
        if plural == "authenticatedlinks":
            return [{"metadata": {"name": "re8ch-artc"}, "spec": {"peerRef": "re8ch-artc"}}]
        return []


def among_spec(**parameters):
    return {"serviceClass": "network.among-clusters.v1", "planRef": "private", "parameters": {"sourceClusterRef": "artc", "destinationClusterRefs": ["re8ch"], "serviceRefs": ["network.egress.mihomo"], **parameters}}


def test_amongclusters_tenant_service_is_limited_to_artchais_mihomos_path(monkeypatch):
    monkeypatch.setattr(controller, "KUBE", AmongClustersKube())
    assert controller.among_clusters_spec("artchais", "mihomos", among_spec()) == among_spec()["parameters"]
    with pytest.raises(PermissionError, match="published for this tenant"):
        controller.among_clusters_spec("re8ch", "mihomos", among_spec())
    with pytest.raises(PermissionError, match="Mihomos"):
        controller.among_clusters_spec("artchais", "mihomos", among_spec(serviceRefs=["kubernetes.control-plane"]))
    with pytest.raises(PermissionError, match="approved artc"):
        controller.among_clusters_spec("artchais", "mihomos", among_spec(destinationClusterRefs=["other"]))


def test_amongclusters_policy_exposes_only_the_reviewed_import():
    instance = {"metadata": {"name": "mihomos", "uid": "instance-uid"}, "spec": {"tenantRef": "artchais"}}
    policy = controller.among_clusters_policy(instance)
    assert policy["metadata"]["namespace"] == "artchais"
    assert policy["spec"] == {"peerSelector": {"matchNames": ["re8ch-artc"]}, "serviceClasses": ["network.egress.mihomo"], "protocols": ["tcp"], "ports": [17891], "directions": ["import"], "maxAdvertisements": 1}
