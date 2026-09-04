import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
CONTROLLER = ROOT / "clusters/re8ch/charts/birfucate-metering/files/controller.py"
SPEC = importlib.util.spec_from_file_location("birfucate_controller", CONTROLLER)
controller = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(controller)


CONFIG = {
    "clusterRef": "re8ch",
    "regionRef": "multi-region",
    "classifications": {
        "internal-control-plane": ["kube-system"],
        "shared-infrastructure-product": ["harbor"],
        "tenant-facing-product": ["qwen"],
        "evaluation-only": ["trial"],
    },
}


def workload(kind="Deployment", namespace="qwen", name="api"):
    return {
        "apiVersion": "apps/v1", "kind": kind,
        "metadata": {"namespace": namespace, "name": name, "uid": f"uid-{namespace}-{name}", "generation": 2},
        "spec": {"replicas": 2, "selector": {"matchLabels": {"app": name}}, "template": {"spec": {"containers": [{"name": "main", "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}}}]}}},
    }


def test_each_inventory_workload_maps_to_one_stable_shard():
    snapshot = json.loads((ROOT / "inventory/platform/live/services.yaml").read_text())
    workloads = [x for x in snapshot["resources"] if x["kind"] in {"Deployment", "StatefulSet", "DaemonSet"}]
    assert len(workloads) == 310
    names = set()
    for item in workloads:
        item = {**item, "apiVersion": "apps/v1", "metadata": {"namespace": item["namespace"], "name": item["name"], "uid": f"uid-{item['kind']}-{item['namespace']}-{item['name']}"}, "spec": {"selector": {"matchLabels": item.get("labels", {})}, "template": {"spec": {"containers": []}}}}
        shard = controller.shard_for(item, {"pods": [], "services": [], "httproutes": []}, CONFIG)
        key = (shard["metadata"]["namespace"], shard["metadata"]["name"])
        assert key not in names
        names.add(key)
        assert shard["spec"]["consumableIntent"] is False
        assert shard["status"]["consumable"] is False


def test_dependency_graph_finds_service_route_pvc_claim_and_node():
    item = workload()
    resources = {
        "pods": [{"metadata": {"namespace": "qwen", "name": "api-1", "labels": {"app": "api"}}, "spec": {"nodeName": "restricted-node-6dc3f140f9", "volumes": [{"persistentVolumeClaim": {"claimName": "data"}}, {"resourceClaim": {"resourceClaimName": "gpu"}}]}}],
        "services": [{"metadata": {"namespace": "qwen", "name": "api"}, "spec": {"selector": {"app": "api"}}}],
        "httproutes": [{"metadata": {"namespace": "qwen", "name": "api-public"}, "spec": {"rules": [{"backendRefs": [{"name": "api"}]}]}}],
    }
    shard = controller.shard_for(item, resources, CONFIG)
    kinds = {(x["relation"], x["kind"], x["name"]) for x in shard["spec"]["dependencies"]}
    assert ("RunsOn", "Node", "restricted-node-6dc3f140f9") in kinds
    assert ("StoresOn", "PersistentVolumeClaim", "data") in kinds
    assert ("Claims", "ResourceClaim", "gpu") in kinds
    assert ("Exposes", "Service", "api") in kinds
    assert ("RoutesTo", "HTTPRoute", "api-public") in kinds


def test_workload_specific_meters_and_draft_conditions():
    stateful = controller.shard_for(workload("StatefulSet", "harbor", "registry"), {"pods": [], "services": [], "httproutes": []}, CONFIG)
    meters = {x["name"] for x in stateful["spec"]["meters"]}
    assert {"provisioned_gib_second", "storage_operation", "artifact_gib_second", "pull_byte", "push_byte", "scan"} <= meters
    conditions = {x["type"]: x["status"] for x in stateful["status"]["conditions"]}
    assert conditions["Discovered"] == "True"
    assert conditions["Consumable"] == "False"


def test_path_contains_no_price_or_high_frequency_samples():
    shard = controller.shard_for(workload(), {"pods": [], "services": [], "httproutes": []}, CONFIG)
    path = controller.path_for(shard)
    assert path["status"]["usage"] == []
    assert "price" not in json.dumps(path).lower()
    assert path["status"]["complete"] is False


class FakeKube:
    def __init__(self, offering_lifecycle):
        self.statuses = []
        self.data = {
            ("apps/v1", "deployments"): [workload()],
            ("v1", "pods"): [], ("v1", "services"): [],
            ("gateway.networking.k8s.io/v1", "httproutes"): [],
            ("batch/v1", "jobs"): [], ("batch/v1", "cronjobs"): [],
            ("apps/v1", "statefulsets"): [], ("apps/v1", "daemonsets"): [],
            ("nuclio.io/v1beta1", "nucliofunctions"): [],
            ("finops.re8ch.com/v1alpha1", "serviceshards"): [],
            ("finops.re8ch.com/v1alpha1", "consumables"): [{"metadata": {"name": "ai-model-gateway"}, "spec": {"lifecycle": offering_lifecycle, "accessContract": {"type": "ModelGateway"}}}],
            ("finops.re8ch.com/v1alpha1", "consumptionbindings"): [{"metadata": {"namespace": "qwen", "name": "tenant-api", "generation": 1}, "spec": {"tenantRef": "qwen", "serviceInstanceRef": "api-1", "consumableRef": "ai-model-gateway", "shardRefs": ["deployment-api"], "access": {"type": "ModelGateway", "parameters": {"models": ["qwen"]}}}}],
        }

    def list(self, api, plural): return self.data.get((api, plural), [])
    def apply(self, plural, obj): return obj
    def patch_status(self, plural, namespace, name, status): self.statuses.append((plural, namespace, name, status))
    def request(self, *args): return {}
    def delete(self, *args): return {}


def test_consumption_binding_never_becomes_ready_before_offering_approval():
    draft = FakeKube("Draft")
    controller.reconcile(draft, CONFIG)
    assert draft.statuses[0][3]["phase"] == "Pending"
    assert draft.statuses[0][3]["conditions"][0]["reason"] == "OfferingNotApproved"

    approved = FakeKube("Approved")
    controller.reconcile(approved, CONFIG)
    assert approved.statuses[0][3]["phase"] == "Ready"


def test_each_infrastructure_service_validates_its_native_tenant_unit():
    offering = lambda kind: {"spec": {"accessContract": {"type": kind}}}
    cases = [
        ({"type": "Grafana", "grafana": {"folderRefs": ["tenant"], "datasourceRefs": ["vm-tenant"], "role": "Viewer"}}, "Grafana"),
        ({"type": "Harbor", "harbor": {"project": "qwen", "permission": "build-push", "quotaGiB": 100, "robotDurationDays": 30}}, "Harbor"),
        ({"type": "Ceph", "ceph": {"mode": "CephFS", "storageClassName": "cephfs-re8ch", "capacityGiB": 20}}, "Ceph"),
        ({"type": "Cilium", "cilium": {"namespaceRef": "qwen", "connectivity": "CrossCluster", "destinationClusters": ["artc"]}}, "Cilium"),
    ]
    for access, kind in cases:
        assert controller.access_valid({"spec": {"access": access}}, offering(kind)) == (True, "AccessContractValid")
    invalid, reason = controller.access_valid({"spec": {"access": {"type": "Harbor", "harbor": {"project": "qwen"}}}}, offering("Harbor"))
    assert invalid is False and reason == "AccessParametersInvalid"
