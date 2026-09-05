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


def binding(tenant, digest="sha256:" + "a" * 64, revoked=False):
    return {"spec": {"tenantRef": tenant, "issuer": "https://dex.re8ch.com", "subjectHash": digest, "revoked": revoked}}


def tenant(name, lifecycle="Active"):
    return {"metadata": {"name": name}, "spec": {"lifecycle": lifecycle}}


def test_identity_resolves_only_one_active_tenant(monkeypatch):
    monkeypatch.setattr(controller, "KUBE", FakeKube([binding("acme")], [tenant("acme")]))
    assert controller.resolve("https://dex.re8ch.com/", "sha256:" + "a" * 64) == "acme"


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
