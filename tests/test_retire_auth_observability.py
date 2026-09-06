from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
OBJECTS = [item for item in yaml.safe_load_all((ROOT / "platform/retire-auth-observability/retirement.yaml").read_text()) if item]


def test_retirement_rbac_is_name_bounded():
    role = next(item for item in OBJECTS if item["kind"] == "ClusterRole")
    names = {name for rule in role["rules"] for name in rule["resourceNames"]}
    assert names == {
        "authentik-server",
        "authentik-worker",
        "authentik-redis",
        "otel-operator-opentelemetry-operator",
        "robusta-forwarder",
        "robusta-runner",
        "trial-gateway-collector",
        "trial-gateway",
    }
    assert all("*" not in rule.get("verbs", []) for rule in role["rules"])


def test_retirement_does_not_target_retained_platforms():
    job = next(item for item in OBJECTS if item["kind"] == "Job")
    script = job["spec"]["template"]["spec"]["containers"][0]["args"][0]
    for retained in ("dex", "nuclio", "litellm", "victoria-metrics"):
        assert retained not in script
    assert "delete ingress/authentik-server" in script
    assert "delete opentelemetrycollector/trial-gateway" in script
    assert "deployment/robusta-runner" in script


def test_retirement_image_is_digest_pinned():
    job = next(item for item in OBJECTS if item["kind"] == "Job")
    image = job["spec"]["template"]["spec"]["containers"][0]["image"]
    assert image.startswith("registry.k8s.io/kubectl:v1.33.4@sha256:")
