import copy
import json
import os
import subprocess
from pathlib import Path

import jsonschema
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPILER = ROOT / "operations/infrastructure-contract/compiler.py"
API_VERSION = "operations.re8ch.com/v1alpha1"


def synthetic_contracts(root: Path) -> None:
    cluster = {
        "apiVersion": API_VERSION,
        "kind": "ClusterContract",
        "metadata": {"name": "re8ch-main", "uid": "10000000-0000-4000-8000-000000000001"},
        "spec": {
            "expectedNodeCount": 1,
            "k3s": {"versionLockRef": "operations/test/version-lock.yaml"},
            "api": {"internalVIP": "192.0.2.211", "publicFQDN": "kube-api.re8ch.com", "publicPort": 6443, "caBundleRef": "secret://cluster-observation/KUBE_API_CA_PEM"},
            "publicAuthorityRef": "operations/network/manifests/network/public-mesh/inventory.json#restricted-node-6dc3f140f9.publicAuthority",
            "authenticationRef": "clusters/re8ch/components/ops/dex-dual-issuer-authentication.yaml",
            "nodeContractsRef": "secret://cluster-observation/INFRASTRUCTURE_CONTRACT_BUNDLE_GZ_B64#nodes.yaml",
            "observationPolicyRef": "secret://cluster-observation/INFRASTRUCTURE_CONTRACT_BUNDLE_GZ_B64#observation-policy.yaml",
        },
    }
    node = {
        "metadata": {"name": "synthetic-node", "uid": "20000000-0000-4000-8000-000000000001"},
        "spec": {
            "kubernetesName": "synthetic-node", "clusterRef": "re8ch-main", "lifecycle": "active",
            "provider": "synthetic", "region": "test-region", "zone": "test-zone", "failureDomain": "test-failure-domain",
            "roles": ["worker"], "services": ["k3s-agent"], "capabilities": ["test"],
            "transport": {"type": "ssh", "address": "node.invalid", "credentialRef": "test/node-ssh", "commands": {"read": ["hostname"], "write": [], "rollback": []}},
            "network": {
                "zeroTierIP": "192.0.2.132", "kubeletInternalIP": "192.0.2.31", "acceleratedIP": "192.0.2.31", "podCIDR": "192.0.2.57/24",
                "legacyNetwork": {"region": "test-region", "zone": "test-zone", "uplinkMbps": 100},
                "fabric": {"role": "leaf", "class": "C", "failureDomain": "test", "provider": "synthetic", "internalIP": "192.0.2.132", "podCIDR": "192.0.2.57/24", "acceleratedIP": "192.0.2.31", "asn": 65001, "publicAuthority": False, "inventoryComplete": True},
            },
            "kubernetes": {"bootstrapLabels": {}, "gitOpsOwnedLabels": {"example.test/owned": "true"}, "controllerDerivedLabelPrefixes": ["kubernetes.io/"], "taints": [], "nodeProfile": "synthetic"},
            "host": {"parameters": {"cpu": 1, "memoryKi": 1024, "ephemeralStorageKi": 2048, "osImage": "Synthetic Linux", "kernelVersion": "1.0"}},
            "observation": {"nodeName": "synthetic-node", "nodeUID": "30000000-0000-4000-8000-000000000001", "facts": ["addresses", "gitOpsOwnedLabels", "taints", "nodeUID", "conditions"]},
            "publishTo": ["compatInventory", "clusterInventory", "advancedFabric"],
        },
    }
    target = {
        "metadata": {"name": "synthetic-target", "uid": "40000000-0000-4000-8000-000000000001"},
        "spec": {"type": "external-ssh", "lifecycle": "active", "transport": {"type": "ssh", "address": "target.invalid", "credentialRef": "test/node-ssh", "commands": {"read": ["hostname"], "write": [], "rollback": []}}, "aliases": [], "capabilities": ["test"], "publishTo": ["compatInventory"]},
    }
    root.mkdir(parents=True)
    (root / "cluster.yaml").write_text(yaml.safe_dump(cluster, sort_keys=False))
    (root / "nodes.yaml").write_text(yaml.safe_dump({"apiVersion": API_VERSION, "kind": "NodeContractList", "items": [node]}, sort_keys=False))
    (root / "targets.yaml").write_text(yaml.safe_dump({"apiVersion": API_VERSION, "kind": "OperationsTargetContractList", "items": [target]}, sort_keys=False))
    (root / "observation-policy.yaml").write_text("apiVersion: operations.re8ch.com/v1alpha1\nkind: NodeObservationPolicy\n")


def schema(name: str) -> dict:
    return json.loads((ROOT / "schemas" / name).read_text())


def test_contract_schemas_reject_unknown_fields_and_inline_credentials(tmp_path):
    contracts = tmp_path / "contracts"
    synthetic_contracts(contracts)
    cluster = yaml.safe_load((contracts / "cluster.yaml").read_text())
    node_doc = yaml.safe_load((contracts / "nodes.yaml").read_text())
    target_doc = yaml.safe_load((contracts / "targets.yaml").read_text())
    node = {"apiVersion": API_VERSION, "kind": "NodeContract", **node_doc["items"][0]}
    target = {"apiVersion": API_VERSION, "kind": "OperationsTargetContract", **target_doc["items"][0]}
    jsonschema.validate(cluster, schema("cluster-contract.schema.json"), format_checker=jsonschema.FormatChecker())
    jsonschema.validate(node, schema("node-contract.schema.json"), format_checker=jsonschema.FormatChecker())
    jsonschema.validate(target, schema("operations-target-contract.schema.json"), format_checker=jsonschema.FormatChecker())
    for key in ("token", "privateKey", "password", "secretValue"):
        invalid = copy.deepcopy(node)
        invalid["spec"][key] = "forbidden"
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid, schema("node-contract.schema.json"))
    cluster["spec"]["unknown"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(cluster, schema("cluster-contract.schema.json"))


def test_compiler_is_deterministic_and_evidence_is_redacted(tmp_path):
    contracts = tmp_path / "contracts"
    synthetic_contracts(contracts)
    outputs = []
    for index in range(2):
        output = tmp_path / f"output-{index}"
        env = {**os.environ, "INFRASTRUCTURE_CONTRACT_DIR": str(contracts), "INFRASTRUCTURE_OUTPUT_DIR": str(output)}
        subprocess.run(["python", str(COMPILER), "render"], check=True, env=env)
        outputs.append({path.name: path.read_bytes() for path in output.iterdir()})
    assert outputs[0] == outputs[1]

    observed = {
        "items": [{
            "metadata": {"name": "synthetic-node", "uid": "ffffffff-ffff-4fff-8fff-ffffffffffff", "labels": {"example.test/owned": "false"}},
            "spec": {"taints": []},
            "status": {"addresses": [{"type": "InternalIP", "address": "192.0.2.198"}], "conditions": [{"type": "Ready", "status": "True"}]},
        }]
    }
    observed_path = tmp_path / "observed.json"
    observed_path.write_text(json.dumps(observed))
    result = subprocess.run(
        ["python", str(COMPILER), "observe-diff", str(observed_path)],
        env={**os.environ, "INFRASTRUCTURE_CONTRACT_DIR": str(contracts)},
        text=True, capture_output=True,
    )
    assert result.returncode == 2
    assert "synthetic-node" not in result.stdout
    assert "192.0.2.198" not in result.stdout
    assert "ffffffff-ffff-4fff-8fff-ffffffffffff" not in result.stdout
    assert '"contractRef": "20000000-0000-4000-8000-000000000001"' in result.stdout


def test_repository_only_contains_sops_encrypted_node_projections():
    subprocess.run(["python", str(COMPILER), "verify-encrypted"], cwd=ROOT, check=True)


def test_oidc_rbac_gateway_and_dns_are_fail_closed():
    rbac = [item for item in yaml.safe_load_all((ROOT / "clusters/re8ch/components/ops/github-actions-node-observer-rbac.yaml").read_text()) if item]
    role = next(item for item in rbac if item["kind"] == "ClusterRole")
    assert [r for r in role["rules"] if r.get("apiGroups") == [""] or "nonResourceURLs" in r] == [
        {"apiGroups": [""], "resources": ["nodes"], "verbs": ["get", "list"]},
        {"nonResourceURLs": ["/api", "/api/*", "/apis", "/apis/*", "/version"], "verbs": ["get"]},
    ]
    serialized = json.dumps(rbac)
    assert all(set(r["verbs"]) <= {"get", "list"} for obj in rbac for r in obj.get("rules", []))
    assert {obj["metadata"]["namespace"] for obj in rbac if obj["kind"] == "Role"} == {"flux-system", "flux-agent-system", "flux-identity-system", "flux-network-system", "flux-observability-system"}
    for forbidden in ("secrets", "pods/log", "pods/exec", "serviceaccounts/token", "impersonate", "create", "patch", "delete"):
        assert forbidden not in serialized

    gateway = [item for item in yaml.safe_load_all((ROOT / "clusters/re8ch/components/cilium/gateway-api-canary.yaml").read_text()) if item]
    public_gateway = next(item for item in gateway if item["kind"] == "Gateway" and item["metadata"]["name"] == "re8ch-gateway-canary")
    listener = next(item for item in public_gateway["spec"]["listeners"] if item["name"] == "kube-api")
    assert listener == {"name": "kube-api", "protocol": "TCP", "port": 6443, "allowedRoutes": {"namespaces": {"from": "Same"}}}
    route = next(item for item in gateway if item["kind"] == "TCPRoute" and item["metadata"]["name"] == "kube-api-public")
    assert route["spec"]["rules"][0]["backendRefs"] == [{"name": "kubernetes", "namespace": "default", "port": 443}]
    dns = next(item for item in gateway if item["kind"] == "Ingress" and item["metadata"]["name"] == "kube-api-public-dns-authority")
    assert dns["metadata"]["annotations"]["external-dns.alpha.kubernetes.io/target"] == "192.0.2.138"


def test_observer_job_is_the_only_oidc_job_and_uses_environment_secrets():
    workflow = yaml.safe_load((ROOT / ".github/workflows/node-contract-observer.yml").read_text())
    observe = workflow["jobs"]["observe"]
    governance = workflow["jobs"]["governance"]
    assert observe["permissions"]["id-token"] == "write"
    assert observe["environment"] == "cluster-observation"
    assert "id-token" not in governance["permissions"]
    text = (ROOT / ".github/workflows/node-contract-observer.yml").read_text()
    assert "secrets.INFRASTRUCTURE_CONTRACT_BUNDLE_GZ_B64" in text
    assert "secrets.KUBE_API_CA_PEM" in text
    assert "vars.INFRASTRUCTURE" not in text
    assert "observer expected 14 Nodes" in text
    for denial_probe in ("secret-list", "pod-log", "pod-exec", "token-request"):
        assert denial_probe in text


def test_observer_rbac_is_in_the_main_flux_root():
    root = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    assert "components/ops/github-actions-node-observer-rbac.yaml" in root["resources"]


def test_release_executor_canary_is_exact_and_reserved_profiles_are_unbound():
    path = ROOT / "clusters/re8ch/components/ops/github-actions-release-executor-rbac.yaml"
    objects = [item for item in yaml.safe_load_all(path.read_text()) if item]
    canary = next(item for item in objects if item["kind"] == "Role")
    assert canary["metadata"]["namespace"] == "api-conformance-runners"
    assert canary["rules"] == [{
        "apiGroups": [""], "resources": ["configmaps"],
        "resourceNames": [
            "api-conformance-runner-projection-contract",
            "re8ch-release-state-api-conformance-runner-projection",
        ],
        "verbs": ["get", "patch", "update"],
    }]
    binding = next(item for item in objects if item["kind"] == "RoleBinding")
    assert binding["subjects"] == [{
        "apiGroup": "rbac.authorization.k8s.io", "kind": "Group",
        "name": "re8ch:release-executor:api-conformance",
    }]
    cluster_roles = [item for item in objects if item["kind"] == "ClusterRole"]
    assert {item["metadata"]["name"] for item in cluster_roles} == {
        "re8ch-release-executor-workload-reserved",
        "re8ch-release-executor-network-policy-reserved",
        "re8ch-release-executor-namespaced-rbac-reserved",
    }
    assert not any(item["kind"] == "ClusterRoleBinding" for item in objects)
    serialized = json.dumps(objects)
    for forbidden in ("secrets", "pods/log", "pods/exec", "serviceaccounts/token", "impersonate"):
        assert forbidden not in serialized
