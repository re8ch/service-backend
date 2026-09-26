from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_dex_rfc8414_route_is_retired_from_flux_kustomization():
    kustomization = yaml.safe_load(
        (ROOT / "platform/cloud-platform/kustomization.yaml").read_text()
    )
    assert "dex-oauth-discovery.yaml" not in kustomization["resources"]


def test_dex_rfc8414_route_reuses_oidc_discovery():
    route = yaml.safe_load(
        (ROOT / "platform/cloud-platform/dex-oauth-discovery.yaml").read_text()
    )
    rule = route["spec"]["rules"][0]
    assert rule["matches"][0]["path"] == {
        "type": "Exact",
        "value": "/.well-known/oauth-authorization-server",
    }
    rewrite = next(item for item in rule["filters"] if item["type"] == "URLRewrite")
    assert rewrite["urlRewrite"]["path"] == {
        "type": "ReplaceFullPath",
        "replaceFullPath": "/.well-known/openid-configuration",
    }
