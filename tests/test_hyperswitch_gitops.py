from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "platform/hyperswitch/release.yaml"
ROUTES = ROOT / "platform/hyperswitch/public-route.yaml"


def objects():
    return list(yaml.safe_load_all(RELEASE.read_text()))


def test_hyperswitch_is_active_internal_sandbox_with_sops_values():
    release = next(item for item in objects() if item["kind"] == "HelmRelease")
    assert release["spec"]["suspend"] is False
    assert release["spec"]["targetNamespace"] == "payments"
    assert release["spec"]["valuesFrom"] == [
        {
            "kind": "Secret",
            "name": "hyperswitch-runtime-values",
            "valuesKey": "values.yaml",
        }
    ]
    values = release["spec"]["values"]
    assert values["hyperswitch-web"]["enabled"] is False
    assert values["hyperswitch-control-center"]["enabled"] is True
    assert values["hyperswitch-control-center"]["ingress"]["enabled"] is False
    features = values["hyperswitch-control-center"]["config"]["default"]["features"]
    assert features["totp"] == "true"
    assert features["email"] == "false"
    assert features["sample_data"] == "false"
    assert values["hyperswitch-app"]["server"]["ingress"]["enabled"] is False
    assert values["hyperswitch-app"]["server"]["run_env"] == "sandbox"


def test_superposition_is_explicit_and_has_no_plaintext_secret():
    release = next(item for item in objects() if item["kind"] == "HelmRelease")
    app = release["spec"]["values"]["hyperswitch-app"]
    assert app["superposition"]["enabled"] is True
    assert app["superpositionDB"]["enable"] is True
    assert app["superpositionFallback"]["source"] == "configMap"
    secretish_keys = {"db" + "_password", "superposition" + "_token", "api" + "_key", "client" + "_secret"}

    def visit(value):
        if isinstance(value, dict):
            assert secretish_keys.isdisjoint(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(release)


def test_superposition_bootstrap_artifacts_are_vendored():
    kustomization = yaml.safe_load(
        (ROOT / "platform/hyperswitch/kustomization.yaml").read_text()
    )
    assert kustomization["generatorOptions"]["disableNameSuffixHash"] is True
    files = {
        item
        for generator in kustomization["configMapGenerator"]
        for item in generator["files"]
    }
    assert any("hyperswitch-v1.126.0-superposition_seed.toml" in item for item in files)
    assert any("superposition-v0.112.0.sql" in item for item in files)
    assert kustomization["resources"].count("superposition-schema-job.yaml") == 1


def test_chart_is_version_pinned_and_latest_is_forbidden():
    release = next(item for item in objects() if item["kind"] == "HelmRelease")
    assert release["spec"]["chart"]["spec"]["version"] == "0.2.26"
    assert "latest" not in RELEASE.read_text().lower()


def test_public_routes_use_dedicated_tls_listeners():
    routes = list(yaml.safe_load_all(ROUTES.read_text()))
    assert {route["spec"]["hostnames"][0] for route in routes} == {
        "pay.re8ch.com",
        "pay-api.re8ch.com",
    }
    for route in routes:
        assert route["kind"] == "HTTPRoute"
        assert len(route["spec"]["parentRefs"]) == 6
        assert all(ref["sectionName"].endswith("-https") for ref in route["spec"]["parentRefs"])


def test_publicedge_only_advertises_hyperswitch_from_r640():
    authority = (ROOT / "clusters/re8ch/components/regional-routing/authority.yaml").read_text()
    assert '"pay.re8ch.com.": {"service":"pay-ui"' in authority
    assert '"pay-api.re8ch.com.": {"service":"pay-api"' in authority
    assert '"pay-ui":["hyperswitch"],"pay-api":["hyperswitch"]' in authority
