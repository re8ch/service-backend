import importlib.util
from pathlib import Path

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
    assert controller.resolve("https://dex.re8ch.com/", "sha256:" + "a" * 64) == {"tenantRef": "acme", "role": "viewer"}


def test_group_identity_resolves_highest_role_for_one_tenant(monkeypatch):
    bindings = [
        group_binding("artchais", "Artchais:tenant-viewers", "viewer"),
        group_binding("artchais", "Artchais:tenant-operators", "operator"),
    ]
    monkeypatch.setattr(controller, "KUBE", FakeKube(bindings, [tenant("artchais")]))
    result = controller.resolve("https://dex.re8ch.com", "", ["Artchais:tenant-viewers", "Artchais:tenant-operators"])
    assert result == {"tenantRef": "artchais", "role": "operator"}


def test_groups_spanning_tenants_are_denied(monkeypatch):
    bindings = [group_binding("artchais", "Artchais:tenant-admins", "admin"), group_binding("other", "Other:tenant-admins", "admin")]
    monkeypatch.setattr(controller, "KUBE", FakeKube(bindings, [tenant("artchais"), tenant("other")]))
    with pytest.raises(PermissionError, match="multiple_tenant_conflict"):
        controller.resolve("https://dex.re8ch.com", "", ["Artchais:tenant-admins", "Other:tenant-admins"])


def test_identity_failure_reasons_are_distinct(monkeypatch):
    monkeypatch.setattr(controller, "KUBE", FakeKube([], [tenant("acme")]))
    with pytest.raises(PermissionError, match="binding_missing"):
        controller.resolve("https://dex.re8ch.com", "sha256:" + "a" * 64)
    monkeypatch.setattr(controller, "KUBE", FakeKube([binding("acme")], [tenant("acme")]))
    with pytest.raises(PermissionError, match="issuer_mismatch"):
        controller.resolve("https://other.example", "sha256:" + "a" * 64)
    monkeypatch.setattr(controller, "KUBE", FakeKube([binding("acme")], [tenant("acme", "Suspended")]))
    with pytest.raises(PermissionError, match="tenant_inactive"):
        controller.resolve("https://dex.re8ch.com", "sha256:" + "a" * 64)


def test_registry_instance_is_tenant_scoped():
    body = {"spec": {"serviceClass": "registry.harbor.v1", "plan": "standard", "parameters": {"project": "acme", "quotaGiB": 10, "robotDurationDays": 30}}}
    value = controller.service_instance_body("acme", "registry", body)
    assert value["spec"]["tenantRef"] == "acme"
    body["spec"]["parameters"]["project"] = "other"
    with pytest.raises(PermissionError):
        controller.service_instance_body("acme", "registry", body)


def test_controller_exposes_only_admin_and_tenant_callers():
    assert controller.ALLOWED_CALLERS == {"admin", "tenant"}


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
