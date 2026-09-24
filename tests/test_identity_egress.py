from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def documents(path):
    return [item for item in yaml.safe_load_all(path.read_text()) if item]


def test_identity_egress_is_ha_digest_pinned_and_gates_google_discovery():
    docs = documents(ROOT / "clusters/re8ch/components/egress-fabric/identity-egress.yaml")
    deployment = next(item for item in docs if item["kind"] == "Deployment")
    service = next(item for item in docs if item["kind"] == "Service")
    budget = next(item for item in docs if item["kind"] == "PodDisruptionBudget")
    container = deployment["spec"]["template"]["spec"]["containers"][0]

    assert deployment["spec"]["replicas"] >= 2
    assert deployment["spec"]["strategy"]["rollingUpdate"] == {"maxSurge": 0, "maxUnavailable": 1}
    assert "@sha256:" in container["image"]
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert deployment["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
    assert deployment["spec"]["template"]["spec"]["hostNetwork"] is True
    assert deployment["spec"]["template"]["spec"]["dnsPolicy"] == "Default"
    init = deployment["spec"]["template"]["spec"]["initContainers"][0]
    assert init["env"][0]["name"] == "POD_IP"
    assert init["env"][0]["valueFrom"]["fieldRef"]["fieldPath"] == "status.podIP"
    assert init["env"][1]["name"] == "NODE_NAME"
    assert init["env"][1]["valueFrom"]["fieldRef"]["fieldPath"] == "spec.nodeName"
    assert "bind-address: ${POD_IP}" in init["args"][0]
    assert '"$NODE_NAME" = restricted-node-6dc3f140f9' in init["args"][0]
    assert container["args"][:2] == ["-d", "/var/lib/mihomo"]
    assert any(volume["name"] == "state" and "emptyDir" in volume for volume in deployment["spec"]["template"]["spec"]["volumes"])
    affinity = deployment["spec"]["template"]["spec"]["affinity"]
    assert "requiredDuringSchedulingIgnoredDuringExecution" in affinity["podAntiAffinity"]
    nodes = affinity["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"]
    assert nodes == ["overseas-kl", "overseas-la", "restricted-node-6dc3f140f9"]
    assert "accounts.google.com%2F.well-known%2Fopenid-configuration" in container["readinessProbe"]["httpGet"]["path"]
    assert service["spec"]["internalTrafficPolicy"] == "Cluster"
    assert "clusterIP" not in service["spec"]
    assert "trafficDistribution" not in service["spec"]
    gitops_service = next(
        item for item in docs
        if item["kind"] == "Service" and item["metadata"]["name"] == "identity-egress-gitops")
    assert gitops_service["spec"]["internalTrafficPolicy"] == "Cluster"
    assert "clusterIP" not in gitops_service["spec"]
    assert "trafficDistribution" not in gitops_service["spec"]
    local_service = next(
        item for item in docs
        if item["kind"] == "Service" and item["metadata"]["name"] == "identity-egress-control-plane")
    assert local_service["spec"]["internalTrafficPolicy"] == "Local"
    assert "clusterIP" not in local_service["spec"]
    assert budget["spec"]["minAvailable"] == 1


def test_identity_egress_is_restricted_to_declared_identity_consumers():
    docs = documents(ROOT / "clusters/re8ch/components/egress-fabric/identity-egress.yaml")
    policy = next(item for item in docs if item["kind"] == "NetworkPolicy")
    rule = policy["spec"]["ingress"][0]
    dex_source, auth_source, flux_source = rule["from"]
    assert dex_source["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"] == "dex"
    expression = dex_source["podSelector"]["matchExpressions"][0]
    assert expression == {
        "key": "app.kubernetes.io/name", "operator": "In", "values": ["dex", "dex-service"]}
    assert auth_source == {
        "namespaceSelector": {
            "matchLabels": {"kubernetes.io/metadata.name": "auth"}},
        "podSelector": {
            "matchLabels": {"app.kubernetes.io/name": "re8ch-auth-gateway"}},
    }
    assert flux_source == {
        "namespaceSelector": {
            "matchLabels": {"kubernetes.io/metadata.name": "flux-system"}},
        "podSelector": {
            "matchLabels": {"app": "source-controller"}},
    }


def test_identity_egress_is_reachable_from_the_cluster_root_overlay():
    root = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    assert "components/egress-fabric/identity-egress.yaml" in root["resources"]
