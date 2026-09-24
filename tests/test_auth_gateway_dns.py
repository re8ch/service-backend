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
    assert env["HTTP_PROXY"] == "http://192.0.2.147:17893"
    assert env["HTTPS_PROXY"] == "http://192.0.2.147:17893"
