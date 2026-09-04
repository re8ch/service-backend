from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "platform/hyperswitch/release.yaml"
ROUTES = ROOT / "platform/hyperswitch/public-route.yaml"


def objects():
    return list(yaml.safe_load_all(RELEASE.read_text()))


def test_hyperswitch_starts_as_internal_suspended_sandbox():
    release = next(item for item in objects() if item["kind"] == "HelmRelease")
    assert release["spec"]["suspend"] is True
    assert release["spec"]["targetNamespace"] == "payments"
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
