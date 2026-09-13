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
    assert controller.resolve("https://dex.re8ch.com/", "sha256:" + "a" * 64) == {"tenantRef": "acme", "role": "viewer", "runtimeNamespaceRef": "acme"}


def test_group_identity_resolves_highest_role_for_one_tenant(monkeypatch):
    bindings = [
        group_binding("artchais", "Artchais:tenant-viewers", "viewer"),
        group_binding("artchais", "Artchais:tenant-operators", "operator"),
    ]
    monkeypatch.setattr(controller, "KUBE", FakeKube(bindings, [tenant("artchais")]))
    result = controller.resolve("https://dex.re8ch.com", "", ["Artchais:tenant-viewers", "Artchais:tenant-operators"])
    assert result == {"tenantRef": "artchais", "role": "operator", "runtimeNamespaceRef": "artchais"}


def test_tenant_publication_excludes_admin_binding(monkeypatch):
    bindings = [
        group_binding("admin", "re8ch:tenant-admins", "admin"),
        group_binding("artchais", "Artchais:tenant-operators", "operator"),
    ]
    monkeypatch.setattr(controller, "KUBE", FakeKube(bindings, [tenant("admin"), tenant("artchais")]))
    groups = ["re8ch:tenant-admins", "Artchais:tenant-operators"]
    assert controller.resolve("https://dex.re8ch.com", "", groups, caller="tenant")["tenantRef"] == "artchais"
    assert controller.resolve("https://dex.re8ch.com", "", groups, caller="admin")["tenantRef"] == "admin"


def test_tenant_publication_rejects_admin_only_identity(monkeypatch):
    monkeypatch.setattr(controller, "KUBE", FakeKube([group_binding("admin", "re8ch:tenant-admins", "admin")], [tenant("admin")]))
    import pytest
    with pytest.raises(PermissionError, match="exactly one"):
        controller.resolve("https://dex.re8ch.com", "", ["re8ch:tenant-admins"], caller="tenant")


def test_groups_spanning_tenants_are_denied(monkeypatch):
    bindings = [group_binding("artchais", "Artchais:tenant-admins", "admin"), group_binding("other", "Other:tenant-admins", "admin")]
    monkeypatch.setattr(controller, "KUBE", FakeKube(bindings, [tenant("artchais"), tenant("other")]))
    with pytest.raises(PermissionError):
        controller.resolve("https://dex.re8ch.com", "", ["Artchais:tenant-admins", "Other:tenant-admins"])


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
