import importlib.util
from pathlib import Path
import sys

import pytest
import yaml

BASE = Path(__file__).parents[1] / "operations/api-conformance"


@pytest.fixture
def controller(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(BASE))
    spec = importlib.util.spec_from_file_location("conformance_controller_test", BASE / "controller.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "APP_ID", "123")
    monkeypatch.setattr(module, "PRIVATE_KEY", tmp_path / "fake-key")
    module.PRIVATE_KEY.touch()
    monkeypatch.setattr(module, "REPOSITORY_INSTALLATIONS", {"re8ch/cloud-functions": "456"})
    monkeypatch.setattr(module, "github_jwt", lambda: "synthetic")
    return module


@pytest.mark.parametrize("repo,installation", [("other/repo", "456"), ("re8ch/cloud-functions", "999"),
                                               ("re8ch/cloud-functions/../other", "456"), ("re8ch/cloud-functions", "../456")])
def test_unreviewed_repository_never_requests_token(controller, monkeypatch, repo, installation):
    def forbidden(*args, **kwargs):
        pytest.fail("network request before repository validation")
    monkeypatch.setattr(controller, "http_json", forbidden)
    with pytest.raises(controller.ContractError):
        controller.installation_token(installation, repo)


def test_token_is_scoped_to_one_repository(controller, monkeypatch):
    calls = []
    def request(url, token, method="GET", body=None):
        calls.append((url, method, body))
        return {"id": 456, "app_id": 123} if method == "GET" else {"token": "synthetic"}
    monkeypatch.setattr(controller, "http_json", request)
    assert controller.installation_token("456", "re8ch/cloud-functions") == "synthetic"
    assert calls[1][2] == {"repositories": ["cloud-functions"], "permissions": controller.APP_PERMISSIONS}


def test_wrong_installation_and_commit_fail_closed(controller, monkeypatch):
    monkeypatch.setattr(controller, "http_json", lambda *a, **k: {"id": 999, "app_id": 123})
    with pytest.raises(controller.ContractError, match="mismatch"):
        controller.installation_token("456", "re8ch/cloud-functions")
    monkeypatch.setattr(controller, "installation_token", lambda *a: "synthetic")
    with pytest.raises(controller.ContractError, match="commit mismatch"):
        controller.publish({"metadata": {"name": "test"}, "spec": {"repository": "re8ch/cloud-functions", "commit": "a" * 40}}, {}, 0)


def test_redirect_and_origin_denied(controller):
    with pytest.raises(controller.ContractError, match="redirects"):
        controller.NoGitHubRedirect().redirect_request(None)
    for url in ["https://evil.invalid", "http://api.github.com", "https://api.github.com@evil.invalid"]:
        with pytest.raises(controller.ContractError, match="origin"):
            controller.http_json(url, "synthetic")


def test_targets_stay_suspended_and_secret_is_encrypted():
    for target in yaml.safe_load_all((BASE / "targets.yaml").read_text()):
        assert target["spec"]["suspend"] is True
        assert target["spec"]["github"]["installationId"] == "171153917"
    secret = yaml.safe_load((BASE.parents[1] / "clusters/re8ch/secrets/api-conformance-github-app.sops.yaml").read_text())
    assert secret["metadata"]["namespace"] == "api-conformance-system"
    assert all(value.startswith("ENC[") for value in secret["stringData"].values())
    assert secret["sops"]["age"]
