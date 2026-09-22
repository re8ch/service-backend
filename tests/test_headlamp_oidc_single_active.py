"""OAuth callback state must remain on Headlamp's single active process."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_headlamp_hpa_cannot_override_single_active_deployment():
    deployment = yaml.safe_load((ROOT / "platform/headlamp/deployment.yaml").read_text())
    policies = list(yaml.safe_load_all((ROOT / "platform/headlamp/availability-policy.yaml").read_text()))
    hpa = next(item for item in policies if item["kind"] == "HorizontalPodAutoscaler")

    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"]["type"] == "Recreate"
    assert hpa["spec"]["scaleTargetRef"]["name"] == deployment["metadata"]["name"]
    assert hpa["spec"]["minReplicas"] == hpa["spec"]["maxReplicas"] == 1
