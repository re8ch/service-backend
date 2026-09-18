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
    assert "neuronpedia.yaml" in resources

    deployments = {
        doc["metadata"]["name"]: doc
        for name in ("workload.yaml", "adapter.yaml", "neuronpedia.yaml")
        for doc in documents(name)
        if doc["kind"] == "Deployment"
    }
    assert "ontology-microscope" in deployments
    assert "ontology-microscope-neuronpedia" in deployments
    assert "ontology-microscope-adapter" in deployments

    for deployment in deployments.values():
        pod_spec = deployment["spec"]["template"]["spec"]
        for container in pod_spec.get("initContainers", []) + pod_spec.get("containers", []):
            if container["name"] == "auth-proxy":
                continue
            assert "@sha256:" in container["image"]


def test_public_route_and_dns_authority_use_neuronpedia_service():
    route = next(doc for doc in documents("public.yaml") if doc["kind"] == "HTTPRoute")
    analysis_rule, default_rule = route["spec"]["rules"]
    assert analysis_rule["matches"] == [
        {"path": {"type": "PathPrefix", "value": "/api/re8ch/analyses"}}
    ]
    assert analysis_rule["backendRefs"] == [
        {"name": "ontology-microscope-adapter", "port": 4318}
    ]
    assert default_rule["backendRefs"] == [
        {"name": "ontology-microscope-neuronpedia", "port": 80}
    ]

    authority = next(doc for doc in documents("public.yaml") if doc["kind"] == "Ingress")
    authority_backends = {
        rule["http"]["paths"][0]["backend"]["service"]["name"]
        for rule in authority["spec"]["rules"]
    }
    assert authority_backends == {"ontology-microscope-neuronpedia"}


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
