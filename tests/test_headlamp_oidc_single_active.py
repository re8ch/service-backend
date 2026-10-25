"""OAuth callback state must remain on Headlamp's single active process."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_headlamp_hpa_cannot_override_single_active_deployment():
    deployment = yaml.safe_load((ROOT / "platform/headlamp/deployment.yaml").read_text())
    policies = list(yaml.safe_load_all((ROOT / "platform/headlamp/availability-policy.yaml").read_text()))
    hpa = next(item for item in policies if item["kind"] == "HorizontalPodAutoscaler")

    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"] == {
        "type": "RollingUpdate",
        "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
    }
    assert hpa["spec"]["scaleTargetRef"]["name"] == deployment["metadata"]["name"]
    assert hpa["spec"]["minReplicas"] == hpa["spec"]["maxReplicas"] == 1


def test_headlamp_direct_api_context_uses_supabase_issuer():
    deployment = yaml.safe_load((ROOT / "platform/headlamp/deployment.yaml").read_text())
    container = next(
        item for item in deployment["spec"]["template"]["spec"]["containers"]
        if item["name"] == "headlamp"
    )

    assert "-in-cluster" in container["args"]
    assert "-oidc-idp-issuer-url=https://db-rw.re8ch.com/auth/v1" in container["args"]
    assert "-oidc-validator-client-id=authenticated" in container["args"]
    assert "-oidc-use-pkce" in container["args"]
    assert "-oidc-use-access-token" in container["args"]
    assert not any(arg.startswith("-oidc-client-secret=") for arg in container["args"])


def test_single_node_rollout_does_not_compete_for_host_ports():
    deployment = yaml.safe_load((ROOT / "platform/headlamp/deployment.yaml").read_text())
    pod = deployment["spec"]["template"]["spec"]
    assert not pod.get("hostNetwork", False)
    for container in pod["containers"]:
        assert all(not port.get("hostPort") for port in container.get("ports", []))
    headlamp = next(c for c in pod["containers"] if c["name"] == "headlamp")
    env = {item["name"]: item["value"] for item in headlamp["env"]}
    assert env["KUBERNETES_SERVICE_HOST"] == "kubernetes.default.svc"
    assert env["KUBERNETES_SERVICE_PORT_HTTPS"] == "443"
    assert pod["hostNetwork"] is False
    assert pod["dnsPolicy"] == "ClusterFirst"
