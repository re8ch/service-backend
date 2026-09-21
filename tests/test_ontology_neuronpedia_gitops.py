from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "platform" / "external-projects" / "ontology-microscope"


def documents(name: str):
    return [doc for doc in yaml.safe_load_all((PROJECT / name).read_text()) if doc]


def test_neuronpedia_canary_is_parallel_and_digest_pinned():
    resources = (PROJECT / "kustomization.yaml").read_text()
    assert "database.yaml" in resources
    assert "adapter.yaml" in resources
    assert "evaluation.yaml" in resources
    assert "neuronpedia.yaml" in resources

    deployments = {
        doc["metadata"]["name"]: doc
        for name in ("workload.yaml", "adapter.yaml", "evaluation.yaml", "neuronpedia.yaml")
        for doc in documents(name)
        if doc["kind"] == "Deployment"
    }
    assert "ontology-microscope" in deployments
    assert "ontology-microscope-neuronpedia" in deployments
    assert "ontology-microscope-adapter" in deployments
    assert "ontology-microscope-evaluation-worker" in deployments

    for name in ("ontology-microscope-neuronpedia", "ontology-microscope-adapter", "ontology-microscope-evaluation-worker"):
        assert deployments[name]["metadata"]["labels"]["scheduling.re8ch.com/placement-managed"] == "true"

    for deployment in deployments.values():
        pod_spec = deployment["spec"]["template"]["spec"]
        for container in pod_spec.get("initContainers", []) + pod_spec.get("containers", []):
            if container["name"] == "auth-proxy":
                continue
            assert "@sha256:" in container["image"]


def test_public_route_uses_tenant_apps_wildcard_and_neuronpedia_service():
    public_documents = documents("public.yaml")
    route = next(doc for doc in public_documents if doc["kind"] == "HTTPRoute")
    assert route["spec"]["hostnames"] == ["ontology.apps.re8ch.com"]
    assert route["spec"]["parentRefs"][0]["sectionName"] == "apps-wildcard-https"
    analysis_rule, default_rule = route["spec"]["rules"]
    assert analysis_rule["matches"] == [
        {"path": {"type": "PathPrefix", "value": "/api/re8ch/"}}
    ]
    assert analysis_rule["backendRefs"] == [
        {"name": "ontology-microscope-adapter", "port": 4318}
    ]
    assert default_rule["backendRefs"] == [
        {"name": "ontology-microscope-neuronpedia", "port": 80}
    ]

    assert all(doc["kind"] != "Ingress" for doc in public_documents)

    deployment = next(doc for doc in documents("neuronpedia.yaml") if doc["kind"] == "Deployment")
    env = {item["name"]: item.get("value") for item in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["NEXT_PUBLIC_URL"] == "https://ontology.apps.re8ch.com"
    assert env["NEXTAUTH_URL"] == "https://ontology.apps.re8ch.com"


def test_database_is_retained_and_secret_is_sops_encrypted():
    stateful_set = next(doc for doc in documents("database.yaml") if doc["kind"] == "StatefulSet")
    claim = stateful_set["spec"]["volumeClaimTemplates"][0]
    assert claim["spec"]["storageClassName"] == "rbd-r640"
    assert claim["spec"]["resources"]["requests"]["storage"] == "20Gi"

    secret_path = ROOT / "clusters" / "re8ch" / "secrets" / "ontology-microscope-db.sops.yaml"
    encrypted = secret_path.read_text()
    secret = yaml.safe_load(encrypted)
    assert "sops:" in encrypted
    assert len(secret["stringData"]) == 6
    assert all(value.startswith("ENC[AES256_GCM") for value in secret["stringData"].values())
    assert "postgresql://" not in encrypted

    evaluation_secret_path = ROOT / "clusters" / "re8ch" / "secrets" / "ontology-microscope-evaluation.sops.yaml"
    evaluation_encrypted = evaluation_secret_path.read_text()
    evaluation_secret = yaml.safe_load(evaluation_encrypted)
    assert "sops:" in evaluation_encrypted
    assert set(evaluation_secret["stringData"]) == {
        "api-tokens-json", "neo4j-user", "neo4j-password", "neo4j-auth"
    }
    assert all(value.startswith("ENC[AES256_GCM") for value in evaluation_secret["stringData"].values())
    assert "woomou-admin" not in evaluation_encrypted


def test_neuronpedia_signin_is_disabled_and_adapter_waits_for_database():
    deployment = next(doc for doc in documents("neuronpedia.yaml") if doc["kind"] == "Deployment")
    containers = {item["name"]: item for item in deployment["spec"]["template"]["spec"]["containers"]}
    env = {item["name"]: item.get("value") for item in containers["webapp"]["env"]}
    assert env["NEXT_PUBLIC_ENABLE_SIGNIN"] == "false"
    assert containers["webapp"]["readinessProbe"]["httpGet"]["path"] == "/"
    assert containers["webapp"]["livenessProbe"]["httpGet"]["path"] == "/icon.ico"
    assert deployment["spec"]["template"]["spec"]["initContainers"][0]["args"] == ["migrate", "deploy"]

    adapter = next(doc for doc in documents("adapter.yaml") if doc["kind"] == "Deployment")
    adapter_container = adapter["spec"]["template"]["spec"]["containers"][0]
    assert adapter_container["readinessProbe"]["httpGet"]["path"] == "/api/health"
    assert any(item["name"] == "NEURONPEDIA_DATABASE_URL" for item in adapter_container["env"])
    assert any(item["name"] == "ONTOLOGY_DATABASE_URL" for item in adapter_container["env"])
    assert any(item["name"] == "ONTOLOGY_API_TOKENS_JSON" for item in adapter_container["env"])
    analysis_key = next(item for item in adapter_container["env"] if item["name"] == "ANALYSIS_OPENAI_API_KEY")
    assert analysis_key["valueFrom"]["secretKeyRef"] == {
        "name": "ontology-microscope-analysis", "key": "api-key", "optional": True
    }

    adapter_egress = next(
        doc
        for doc in documents("adapter.yaml")
        if doc["kind"] == "CiliumNetworkPolicy"
    )
    assert adapter_egress["spec"]["egress"] == [{"toEntities": ["cluster"]}]
    assert adapter_egress["spec"]["ingress"][0]["toPorts"][0]["ports"] == [
        {"port": "4318", "protocol": "TCP"}
    ]


def test_public_neuronpedia_service_bypasses_basic_auth_proxy():
    documents_in_file = documents("neuronpedia.yaml")
    deployment = next(doc for doc in documents_in_file if doc["kind"] == "Deployment")
    service = next(doc for doc in documents_in_file if doc["kind"] == "Service")
    policy = next(doc for doc in documents_in_file if doc["kind"] == "CiliumNetworkPolicy")

    pod_spec = deployment["spec"]["template"]["spec"]
    assert [container["name"] for container in pod_spec["containers"]] == ["webapp"]
    assert {volume["name"] for volume in pod_spec["volumes"]} == {"tmp"}
    assert service["spec"]["ports"] == [
        {"name": "http", "port": 80, "targetPort": "webapp", "protocol": "TCP"}
    ]
    assert policy["spec"]["ingress"][0]["toPorts"][0]["ports"] == [
        {"port": "3000", "protocol": "TCP"}
    ]
    assert "auth_basic" not in (PROJECT / "neuronpedia.yaml").read_text()


def test_ui_less_evaluation_worker_uses_litellm_postgres_and_private_neo4j():
    docs = documents("evaluation.yaml")
    worker = next(doc for doc in docs if doc["kind"] == "Deployment")
    stateful_set = next(doc for doc in docs if doc["kind"] == "StatefulSet")
    service = next(doc for doc in docs if doc["kind"] == "Service")
    container = worker["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item for item in container["env"]}

    assert container["command"] == ["node", "--import", "tsx", "server/evaluation-worker.ts"]
    assert env["EVALUATION_OPENAI_BASE_URL"]["value"] == "http://litellm.llm-gateway.svc.cluster.local:4000/v1"
    assert env["EVALUATION_OPENAI_MODEL"]["value"] == "deepseek-v4-flash"
    assert env["ONTOLOGY_DATABASE_URL"]["valueFrom"]["secretKeyRef"]["name"] == "ontology-microscope-db"
    assert env["EVALUATION_NEO4J_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == "ontology-microscope-evaluation"
    assert service["spec"]["ports"] == [
        {"name": "bolt", "port": 7687, "targetPort": "bolt", "protocol": "TCP"}
    ]
    assert stateful_set["spec"]["volumeClaimTemplates"][0]["spec"]["storageClassName"] == "rbd-r640"
    assert stateful_set["spec"]["volumeClaimTemplates"][0]["spec"]["resources"]["requests"]["storage"] == "20Gi"

    policies = [doc for doc in docs if doc["kind"] == "CiliumNetworkPolicy"]
    neo4j_policy = next(doc for doc in policies if doc["metadata"]["name"] == "ontology-microscope-neo4j")
    assert neo4j_policy["spec"].get("egress") is None
    assert neo4j_policy["spec"]["ingress"][0]["toPorts"][0]["ports"] == [
        {"port": "7687", "protocol": "TCP"}
    ]
