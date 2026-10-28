from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_auth_gateway_uses_stable_cluster_dns_service():
    deployment = yaml.safe_load(
        (ROOT / "clusters/re8ch/components/auth/deployments/re8ch-auth-gateway.yaml").read_text()
    )
    pod = deployment["spec"]["template"]["spec"]
    assert pod["dnsPolicy"] == "None"
    assert pod["dnsConfig"]["nameservers"] == ["10.43.0.10"]
    assert pod["dnsConfig"]["searches"] == [
        "auth.svc.cluster.local",
        "svc.cluster.local",
        "cluster.local",
    ]
    env = {item["name"]: item.get("value") for item in pod["containers"][0]["env"]}
    assert env["HTTP_PROXY"] == "http://mihomos-node-cn-local.egress-fabric.svc.cluster.local:17891"
    assert env["HTTPS_PROXY"] == "http://mihomos-node-cn-local.egress-fabric.svc.cluster.local:17891"


def test_auth_gateway_rollout_isolates_degraded_local_podcidr_and_preserves_session_verification():
    deployment = yaml.safe_load((ROOT / "clusters/re8ch/components/auth/deployments/re8ch-auth-gateway.yaml").read_text())
    spec = deployment["spec"]
    assert spec["replicas"] == 1
    assert spec["strategy"] == {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}}
    pod = spec["template"]["spec"]
    assert pod["terminationGracePeriodSeconds"] > 30
    env = {item["name"]: item.get("value") for item in pod["containers"][0]["env"]}
    assert env["IDENTITY_AUTH_URL"].startswith("https://")
    assert env["IDENTITY_USER_URL"] == "http://supabase-identity-auth.supabase-identity.svc.cluster.local:9999/user"
    pdb = yaml.safe_load((ROOT / "clusters/re8ch/components/auth/deployments/re8ch-auth-gateway-pdb.yaml").read_text())
    assert pdb["spec"]["minAvailable"] == 1
    assert pdb["spec"]["selector"] == spec["selector"]


def test_session_calls_use_cluster_services_and_other_auth_calls_use_publicedge():
    deployment = yaml.safe_load((ROOT / "clusters/re8ch/components/auth/deployments/re8ch-auth-gateway.yaml").read_text())
    env = {item["name"]: item.get("value") for item in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["IDENTITY_RO_URL"] == "http://supabase-identity-rest-ro.supabase-identity.svc.cluster.local:3000"
    assert env["IDENTITY_RW_URL"] == "http://supabase-identity-rest.supabase-identity.svc.cluster.local:3000"
    assert env["MGR_RPC_RO_BASE_URL"] == "https://db-ro.re8ch.com/auth"
    assert env["MGR_RPC_RW_BASE_URL"] == "https://db-rw.re8ch.com/auth"
    assert {"api.re8ch.com", "db-ro.re8ch.com", "db-rw.re8ch.com"}.issubset(set(env["NO_PROXY"].split(",")))
    release = yaml.safe_load((ROOT / "platform/birfucate-metering/release.yaml").read_text())
    api = release["spec"]["values"]["tenantPlatform"]["tenantAPI"]
    assert api["platformSessionURL"] == (
        "http://re8ch-auth-gateway.auth.svc.cluster.local:8080/auth/v2/platform/session"
    )
    assert api["organizationsURL"] == "https://api.re8ch.com/auth/v2/organizations"
