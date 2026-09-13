from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def objects():
    path = ROOT / "clusters/re8ch/flux-system/github-control-plane.yaml"
    return {item["kind"]: item for item in yaml.safe_load_all(path.read_text())}


def test_github_flux_canary_is_enabled_before_promotion():
    docs = objects()
    source = docs["GitRepository"]
    assert source["spec"]["suspend"] is False
    assert source["spec"]["provider"] == "github"
    assert source["spec"]["url"] == "https://github.com/re8ch/service-backend.git"
    assert source["spec"]["verify"]["mode"] == "HEAD"
    assert docs["Kustomization"]["spec"]["suspend"] is False
    assert docs["Receiver"]["spec"]["suspend"] is False
    assert docs["Provider"]["spec"]["suspend"] is False
    assert docs["Alert"]["spec"]["suspend"] is False


def test_existing_root_source_remains_rollback_capable_until_promotion():
    current = list(yaml.safe_load_all((ROOT / "clusters/re8ch/flux-system/gotk-sync.yaml").read_text()))[0]
    assert current["kind"] == "GitRepository"
    assert current["spec"]["url"] == "https://gitlab.com/reachievellc/service-backend.git"
    assert current["spec"]["secretRef"]["name"] == "service-backend-flux-auth"


def test_no_secret_values_are_committed_in_control_plane_manifest():
    text = (ROOT / "clusters/re8ch/flux-system/github-control-plane.yaml").read_text()
    assert "kind: Secret" not in text
    assert "githubAppPrivateKey" not in text
    assert "token:" not in text
