from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_auth_gateway_uses_stable_cluster_dns_service():
    deployment = yaml.safe_load(
        (ROOT / "clusters/re8ch/components/auth/deployments/re8ch-auth-gateway.yaml").read_text()
    )
    pod = deployment["spec"]["template"]["spec"]
    assert pod["dnsPolicy"] == "None"
    assert pod["dnsConfig"]["nameservers"] == ["192.0.2.240"]
    assert pod["dnsConfig"]["searches"] == [
        "auth.svc.cluster.local",
        "svc.cluster.local",
        "cluster.local",
    ]
    env = {item["name"]: item.get("value") for item in pod["containers"][0]["env"]}
    assert env["HTTP_PROXY"] == "http://mihomos-node-cn-local.egress-fabric.svc.cluster.local:17891"
    assert env["HTTPS_PROXY"] == "http://mihomos-node-cn-local.egress-fabric.svc.cluster.local:17891"


def test_auth_gateway_rollout_preserves_capacity_and_internal_verification():
    deployment = yaml.safe_load((ROOT / "clusters/re8ch/components/auth/deployments/re8ch-auth-gateway.yaml").read_text())
    spec = deployment["spec"]
    assert spec["replicas"] == 2
    assert spec["strategy"] == {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}}
    pod = spec["template"]["spec"]
    assert pod["terminationGracePeriodSeconds"] > 30
    env = {item["name"]: item.get("value") for item in pod["containers"][0]["env"]}
    assert env["IDENTITY_AUTH_URL"].startswith("https://")
    assert env["IDENTITY_USER_URL"] == "http://supabase-identity-auth.supabase-identity.svc:9999/user"
    pdb = yaml.safe_load((ROOT / "clusters/re8ch/components/auth/deployments/re8ch-auth-gateway-pdb.yaml").read_text())
    assert pdb["spec"]["minAvailable"] == 1
    assert pdb["spec"]["selector"] == spec["selector"]
