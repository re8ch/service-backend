from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "clusters/re8ch/components/ai-operations/stagehand-service.yaml"


def objects():
    return list(yaml.safe_load_all(MANIFEST.read_text()))


def test_stagehand_is_private_digest_pinned_and_reuses_cluster_gateways():
    resources = objects()
    deployment = next(item for item in resources if item["kind"] == "Deployment")
    service = next(item for item in resources if item["kind"] == "Service")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item for item in container["env"]}

    assert service["spec"]["type"] == "ClusterIP"
    assert "@sha256:" in container["image"] and ":latest" not in container["image"]
    assert env["STAGEHAND_MODEL"]["value"] == "openai/kimi-k2-5"
    assert env["LITELLM_BASE_URL"]["value"] == "http://litellm.llm-gateway.svc.cluster.local:4000/v1"
    assert env["STEEL_URL"]["value"] == "http://steel-browser.ai-operations.svc.cluster.local:3000"
    assert env["LITELLM_API_KEY"]["valueFrom"]["secretKeyRef"]["name"] == "cluster-infra-litellm"
    assert env["STAGEHAND_API_TOKEN"]["valueFrom"]["secretKeyRef"]["name"] == "cluster-infra-litellm"


def test_stagehand_network_policy_only_allows_required_cluster_services():
    policy = next(item for item in objects() if item["kind"] == "NetworkPolicy")
    assert policy["spec"]["policyTypes"] == ["Ingress", "Egress"]
    ports = {entry["port"] for rule in policy["spec"]["egress"] for entry in rule["ports"]}
    assert ports == {53, 3000, 4000}
