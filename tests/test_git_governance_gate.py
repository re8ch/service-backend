from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def documents(path: str):
    return [item for item in yaml.safe_load_all((ROOT / path).read_text()) if item]


def test_owner_approval_is_immutable_and_requires_merged_pr():
    crds = {item["metadata"]["name"]: item for item in documents("platform/git-workspaces/crds.yaml")}
    schema = crds["ownerapprovals.governance.re8ch.com"]["spec"]["versions"][0]["schema"]["openAPIV3Schema"]
    approval = schema["properties"]["spec"]
    assert approval["x-kubernetes-validations"][0]["rule"] == "self == oldSelf"
    assert approval["properties"]["pullRequest"]["properties"]["merged"]["enum"] == [True]


def test_capability_and_approval_writers_are_fail_closed():
    policies = {
        item["metadata"]["name"]: item
        for item in documents("platform/git-workspaces/owner-approval-admission.yaml")
        if item["kind"] == "ValidatingAdmissionPolicy"
    }
    assert "owner-review-verifier" in policies["owner-approval-producer"]["spec"]["validations"][0]["expression"]
    assert "git-workspace-controller" in policies["project-capability-producer"]["spec"]["validations"][0]["expression"]
    status_rules = policies["git-account-status-writers"]["spec"]["validations"]
    assert any("mailbox-provisioner" in rule["expression"] for rule in status_rules)
    assert any("git-workspace-controller" in rule["expression"] for rule in status_rules)


def test_pilot_workspace_uses_locked_controller_and_owner_gate_capabilities():
    pilot = documents("platform/workspace-controller/re8ch-owner-pilot.yaml")[0]
    pod = pilot["spec"]["template"]["spec"]
    assert pod["nodeSelector"]["kubernetes.io/hostname"] == "restricted-node-6dc3f140f9"
    assert pod["containers"][0]["args"][-2:] == ["re8ch-owner-pilot-20260825", "re8ch-core"]
    controller = (ROOT / "platform/git-workspaces/controller.yaml").read_text()
    assert 'mailbox.get("phase") == "Ready" and approved' in controller
    assert '["project-write", "edgeone-deploy"]' in controller
