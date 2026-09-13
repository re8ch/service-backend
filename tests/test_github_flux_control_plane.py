from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def objects():
    path = ROOT / "clusters/re8ch/flux-system/github-control-plane.yaml"
    return {item["kind"]: item for item in yaml.safe_load_all(path.read_text())}


def test_github_flux_reactions_target_promoted_source():
    docs = objects()
    assert docs["Receiver"]["spec"]["suspend"] is False
    assert docs["Receiver"]["spec"]["resources"] == [
        {"apiVersion": "source.toolkit.fluxcd.io/v1", "kind": "GitRepository", "name": "service-backend"}
    ]
    assert docs["Provider"]["spec"]["suspend"] is False
    assert docs["Alert"]["spec"]["suspend"] is False
    assert docs["Alert"]["spec"]["eventSources"] == [
        {"kind": "GitRepository", "name": "service-backend"},
        {"kind": "Kustomization", "name": "service-backend"},
    ]


def test_root_source_is_github_authoritative_after_promotion():
    current = list(yaml.safe_load_all((ROOT / "clusters/re8ch/flux-system/gotk-sync.yaml").read_text()))[0]
    assert current["kind"] == "GitRepository"
    assert current["spec"]["provider"] == "github"
    assert current["spec"]["url"] == "https://github.com/re8ch/service-backend.git"
    assert current["spec"]["secretRef"]["name"] == "service-backend-github-app-auth"


def test_no_secret_values_are_committed_in_control_plane_manifest():
    text = (ROOT / "clusters/re8ch/flux-system/github-control-plane.yaml").read_text()
    assert "kind: Secret" not in text
    assert "githubAppPrivateKey" not in text
    assert "token:" not in text
