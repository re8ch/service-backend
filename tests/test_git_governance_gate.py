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
    assert all(volume["name"] != "credentials" for volume in pod["volumes"])
    assert all("GIT_SSH_COMMAND" not in {item["name"] for item in container.get("env", [])} for container in [*pod["initContainers"], *pod["containers"]])
    prepare = pod["initContainers"][0]
    assert prepare["name"] == "prepare-directories"
    assert prepare["securityContext"]["capabilities"]["add"] == ["CHOWN"]
    sanitize = pod["initContainers"][1]
    assert sanitize["name"] == "remove-invalid-pilot-state"
    assert "rev-parse --is-bare-repository" in sanitize["command"][-1]
    mirror = documents("platform/workspace-controller/r640-mirror.yaml")[1]
    assert mirror["kind"] == "CronJob"
    assert mirror["spec"]["suspend"] is True
    controller = (ROOT / "platform/git-workspaces/controller.yaml").read_text()
    assert 'mailbox.get("phase") == "Ready" and approved' in controller
    assert '["project-write", "origin-branch", "collaboration-read-write"]' in controller


def test_bot_employee_requires_provider_and_collaboration_identity():
    crds = {item["metadata"]["name"]: item for item in documents("platform/git-workspaces/crds.yaml")}
    schema = crds["gitaccounts.governance.re8ch.com"]["spec"]["versions"][0]["schema"]["openAPIV3Schema"]
    spec = schema["properties"]["spec"]
    assert any("Bot employees require" in rule["message"] for rule in spec["x-kubernetes-validations"])
    capabilities = crds["projectcapabilities.governance.re8ch.com"]["spec"]["versions"][0]["schema"]["openAPIV3Schema"]
    values = capabilities["properties"]["spec"]["properties"]["capabilities"]["items"]["enum"]
    assert {"origin-branch", "collaboration-read-write"} <= set(values)


def test_pilot_waits_for_human_owner_and_provider_account():
    resources = documents("platform/git-workspaces/repo-pilot-bot.yaml")
    account = next(item for item in resources if item["kind"] == "GitAccount")
    assert account["spec"]["email"] == "repo-pilot-bot@phonaid.com"
    assert account["spec"]["providerIdentity"]["username"] == "re8ch-repo-pilot-bot"
    assert account["metadata"]["annotations"]["governance.re8ch.com/onboarding-state"] == "awaiting-provider-account-and-owner-pr"
    assert not any(item["kind"] == "OwnerApproval" for item in resources)
