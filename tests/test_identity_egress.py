from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def documents(path):
    return [item for item in yaml.safe_load_all(path.read_text()) if item]


def test_identity_egress_is_ha_digest_pinned_and_gates_real_github_path():
    docs = documents(ROOT / "clusters/re8ch/components/egress-fabric/identity-egress.yaml")
    deployment = next(item for item in docs if item["kind"] == "Deployment")
    service = next(item for item in docs if item["kind"] == "Service")
    budget = next(item for item in docs if item["kind"] == "PodDisruptionBudget")
    container = deployment["spec"]["template"]["spec"]["containers"][0]

    assert deployment["spec"]["replicas"] >= 2
    assert deployment["spec"]["strategy"]["rollingUpdate"]["maxUnavailable"] == 0
    assert "@sha256:" in container["image"]
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert "github.com%2Flogin%2Foauth%2Faccess_token" in container["readinessProbe"]["httpGet"]["path"]
    assert service["spec"]["internalTrafficPolicy"] == "Cluster"
    assert budget["spec"]["minAvailable"] == 1


def test_identity_egress_is_restricted_to_dex_ingress():
    policy = documents(ROOT / "clusters/re8ch/components/egress-fabric/network-policy.yaml")[0]
    rule = next(
        item for item in policy["spec"]["ingress"]
        if any(port["port"] == 17893 for port in item.get("ports", []))
    )
    source = rule["from"][0]
    assert source["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"] == "dex"
    assert source["podSelector"]["matchLabels"]["app.kubernetes.io/name"] == "dex"
