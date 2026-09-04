from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "platform/hyperswitch/release.yaml"


def objects():
    return list(yaml.safe_load_all(RELEASE.read_text()))


def test_hyperswitch_starts_as_internal_suspended_sandbox():
    release = next(item for item in objects() if item["kind"] == "HelmRelease")
    assert release["spec"]["suspend"] is True
    assert release["spec"]["targetNamespace"] == "payments"
    values = release["spec"]["values"]
    assert values["hyperswitch-web"]["enabled"] is False
    assert values["hyperswitch-control-center"]["enabled"] is False
    assert values["hyperswitch-app"]["server"]["ingress"]["enabled"] is False
    assert values["hyperswitch-app"]["server"]["run_env"] == "sandbox"


def test_superposition_is_explicit_and_has_no_plaintext_secret():
    release = next(item for item in objects() if item["kind"] == "HelmRelease")
    app = release["spec"]["values"]["hyperswitch-app"]
    assert app["superposition"]["enabled"] is True
    assert app["superpositionDB"]["enable"] is True
    assert app["superpositionFallback"]["source"] == "configMap"
    serialized = yaml.safe_dump(release)
    for forbidden in ("db_password:", "superposition_token:", "api_key:", "client_secret:"):
        assert forbidden not in serialized


def test_chart_is_version_pinned_and_latest_is_forbidden():
    release = next(item for item in objects() if item["kind"] == "HelmRelease")
    assert release["spec"]["chart"]["spec"]["version"] == "0.2.26"
    assert "latest" not in RELEASE.read_text().lower()
