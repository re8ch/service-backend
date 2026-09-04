#!/usr/bin/env python3
"""Materialize one ServiceShard and BifurcationPath per Kubernetes workload."""

import hashlib
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request


GROUP = "finops.re8ch.com"
VERSION = "v1alpha1"
FINALIZER = "finops.re8ch.com/workload-observed"
WORKLOADS = (
    ("apps/v1", "deployments", "Deployment"),
    ("apps/v1", "statefulsets", "StatefulSet"),
    ("apps/v1", "daemonsets", "DaemonSet"),
    ("batch/v1", "jobs", "Job"),
    ("batch/v1", "cronjobs", "CronJob"),
    ("nuclio.io/v1beta1", "nucliofunctions", "NuclioFunction"),
)
DIMENSIONS = ["tenant_ref", "service_instance_ref", "resource_ref", "cluster_ref", "region_ref"]


def stable_name(kind, name):
    base = f"{kind.lower()}-{name}".replace("_", "-")
    if len(base) <= 63:
        return base
    return base[:54].rstrip("-") + "-" + hashlib.sha256(base.encode()).hexdigest()[:8]


def classification(namespace, config):
    for class_name, namespaces in config["classifications"].items():
        if namespace in namespaces:
            return class_name
    return "evaluation-only"


def meter_contract(kind, namespace):
    common = [("cpu_core_second", "core-second"), ("memory_gib_second", "GiB-second"), ("network_egress_byte", "byte")]
    if kind == "StatefulSet":
        common += [("provisioned_gib_second", "GiB-second"), ("storage_operation", "operation"), ("connection_second", "connection-second")]
    elif kind == "DaemonSet":
        common += [("node_agent_second", "agent-second")]
    elif kind in {"Job", "CronJob"}:
        common += [("execution_second", "second"), ("execution_result", "result")]
    else:
        common += [("request", "request"), ("concurrency_second", "concurrent-second")]
    if namespace == "harbor":
        common += [("artifact_gib_second", "GiB-second"), ("pull_byte", "byte"), ("push_byte", "byte"), ("scan", "scan")]
    if namespace in {"qianwen-ops", "observability-ai"}:
        common += [("sample_ingested", "sample"), ("log_byte_ingested", "byte"), ("retained_gib_second", "GiB-second")]
    if namespace in {"egress-fabric", "regional-routing", "among-clusters"}:
        common += [("path_second", "path-second"), ("flow", "flow"), ("route_change", "change")]
    if namespace in {"llm-gateway", "qwen"}:
        common += [("input_token", "token"), ("output_token", "token"), ("gpu_second", "GPU-second")]
    return [{"name": name, "unit": unit, "requiredDimensions": DIMENSIONS} for name, unit in dict(common).items()]


def consumable_for(namespace):
    if namespace == "harbor": return "registry-harbor"
    if namespace in {"qianwen-ops", "observability-ai"}: return "observability-managed"
    if namespace in {"egress-fabric", "regional-routing", "among-clusters"}: return "network-among-clusters"
    if namespace in {"rook-ceph", "storage-fabric"}: return "storage-volume"
    if namespace in {"pigsty-external", "supabase-pg-re8ch"}: return "database-postgresql-shared"
    if namespace in {"llm-gateway", "qwen"}: return "ai-model-gateway"
    if namespace in {"opensandbox-system", "codex-platform", "codex-app-server"}: return "sandbox-runtime"
    return "compute-workload"


def access_valid(binding, consumable):
    requested = binding.get("spec", {}).get("access", {})
    contract = (consumable or {}).get("spec", {}).get("accessContract", {})
    kind = requested.get("type")
    if not kind or kind != contract.get("type"):
        return False, "AccessContractMismatch"
    if kind == "Grafana":
        value = requested.get("grafana", {})
        valid = bool(value.get("datasourceRefs")) and bool(value.get("organizationRef") or value.get("folderRefs")) and value.get("role") in {"Viewer", "Editor"}
    elif kind == "Harbor":
        value = requested.get("harbor", {})
        valid = bool(value.get("project")) and value.get("permission") in {"pull", "build-push", "project-admin"} and value.get("quotaGiB", 0) > 0 and 0 < value.get("robotDurationDays", 0) <= 365
    elif kind == "Ceph":
        value = requested.get("ceph", {})
        mode = value.get("mode")
        valid = (mode in {"RBD", "CephFS"} and bool(value.get("storageClassName")) and value.get("capacityGiB", 0) > 0) or (mode == "S3" and bool(value.get("bucketClass")))
    elif kind == "Cilium":
        value = requested.get("cilium", {})
        connectivity = value.get("connectivity")
        valid = bool(value.get("namespaceRef")) and connectivity in {"ClusterLocal", "CrossCluster", "PublicEdge"} and (connectivity != "CrossCluster" or bool(value.get("destinationClusters")))
    else:
        valid = isinstance(requested.get("parameters", {}), dict)
    return (True, "AccessContractValid") if valid else (False, "AccessParametersInvalid")


def selectors(workload):
    spec = workload.get("spec", {})
    if workload["kind"] == "CronJob":
        return spec.get("jobTemplate", {}).get("spec", {}).get("template", {}).get("metadata", {}).get("labels", {})
    return spec.get("selector", {}).get("matchLabels", {})


def matches(labels, wanted):
    return bool(wanted) and all(labels.get(k) == v for k, v in wanted.items())


def dependencies(workload, resources):
    meta, ns = workload["metadata"], workload["metadata"]["namespace"]
    wanted = selectors(workload)
    out, ambiguous = [], []
    pods = [x for x in resources.get("pods", []) if x["metadata"].get("namespace") == ns and matches(x["metadata"].get("labels", {}), wanted)]
    for pod in pods:
        if pod.get("spec", {}).get("nodeName"):
            out.append(("RunsOn", "v1", "Node", "", pod["spec"]["nodeName"]))
        for volume in pod.get("spec", {}).get("volumes", []):
            claim = volume.get("persistentVolumeClaim", {}).get("claimName")
            resource_claim = volume.get("resourceClaim", {}).get("resourceClaimName")
            if claim:
                out.append(("StoresOn", "v1", "PersistentVolumeClaim", ns, claim))
            if resource_claim:
                out.append(("Claims", "resource.k8s.io/v1", "ResourceClaim", ns, resource_claim))
    services = [x for x in resources.get("services", []) if x["metadata"].get("namespace") == ns and matches(wanted, x.get("spec", {}).get("selector", {}))]
    for service in services:
        out.append(("Exposes", "v1", "Service", ns, service["metadata"]["name"]))
        same_name = [x for x in services if x["metadata"]["name"] == service["metadata"]["name"]]
        if len(same_name) > 1:
            ambiguous.append(f"Service/{service['metadata']['name']}")
    service_names = {x["metadata"]["name"] for x in services}
    for route in resources.get("httproutes", []):
        if route["metadata"].get("namespace") != ns:
            continue
        refs = [b for rule in route.get("spec", {}).get("rules", []) for b in rule.get("backendRefs", [])]
        if any(ref.get("name") in service_names for ref in refs):
            out.append(("RoutesTo", "gateway.networking.k8s.io/v1", "HTTPRoute", ns, route["metadata"]["name"]))
    unique = sorted(set(out))
    return [{"relation": a, "apiVersion": b, "kind": c, "namespace": d, "name": e} for a, b, c, d, e in unique], ambiguous


def shard_for(workload, resources, config):
    meta, spec = workload["metadata"], workload.get("spec", {})
    ns, kind = meta["namespace"], workload["kind"]
    deps, ambiguous = dependencies(workload, resources)
    cls = classification(ns, config)
    containers = spec.get("template", {}).get("spec", {}).get("containers", [])
    if kind == "CronJob":
        containers = spec.get("jobTemplate", {}).get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    requested = {"containers": [{"name": c.get("name"), "requests": c.get("resources", {}).get("requests", {})} for c in containers]}
    return {
        "apiVersion": f"{GROUP}/{VERSION}", "kind": "ServiceShard",
        "metadata": {"name": stable_name(kind, meta["name"]), "namespace": ns, "finalizers": [FINALIZER], "labels": {"app.kubernetes.io/managed-by": "birfucate-discovery", "finops.re8ch.com/classification": cls, "finops.re8ch.com/workload-kind": kind.lower()}},
        "spec": {"workloadRef": {"apiVersion": workload["apiVersion"], "kind": kind, "name": meta["name"], "uid": meta["uid"]}, "consumableRef": consumable_for(ns), "clusterRef": config["clusterRef"], "regionRef": meta.get("labels", {}).get("topology.kubernetes.io/region", config["regionRef"]), "classification": cls, "consumableIntent": False, "placement": {"replicas": spec.get("replicas"), "nodes": sorted({x["name"] for x in deps if x["kind"] == "Node"})}, "resources": requested, "dependencies": deps, "meters": meter_contract(kind, ns)},
        "status": {"observedGeneration": meta.get("generation", 1), "consumable": False, "meterCoveragePercent": 0, "conditions": conditions(ambiguous), "ambiguousDependencies": ambiguous},
    }


def conditions(ambiguous):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    def c(t, ok, reason): return {"type": t, "status": "True" if ok else "False", "reason": reason, "lastTransitionTime": now}
    return [c("Discovered", True, "WorkloadObserved"), c("Measurable", False, "UsageDimensionsUnverified"), c("Isolated", False, "OwnerApprovalRequired"), c("Consumable", False, "DraftOnly"), c("Degraded", bool(ambiguous), "AmbiguousDependency" if ambiguous else "NoKnownDegradation")]


def path_for(shard, binding=None):
    ns, name = shard["metadata"]["namespace"], shard["metadata"]["name"]
    root = f"shard:{ns}/{name}"
    nodes = [{"id": root, "kind": "ServiceShard", "namespace": ns, "name": name, "state": "Draft"}]
    edges = []
    for dep in shard["spec"]["dependencies"]:
        node_id = f"{dep['kind']}:{dep['namespace']}/{dep['name']}"
        nodes.append({"id": node_id, "kind": dep["kind"], "namespace": dep["namespace"], "name": dep["name"], "state": "Observed"})
        edges.append({"from": root, "to": node_id, "relation": dep["relation"]})
    spec = {"shardRef": name}
    if binding:
        spec.update({"tenantRef": binding["spec"]["tenantRef"], "serviceInstanceRef": binding["spec"]["serviceInstanceRef"], "bindingRef": binding["metadata"]["name"]})
        instance = f"instance:{binding['spec']['serviceInstanceRef']}"
        usage = f"usage:{binding['spec']['serviceInstanceRef']}"
        nodes += [{"id": instance, "kind": "TenantServiceInstance", "namespace": ns, "name": binding["spec"]["serviceInstanceRef"], "state": "Bound"}, {"id": usage, "kind": "UsageEvent", "namespace": "", "name": binding["spec"]["serviceInstanceRef"], "state": "PendingMeters"}]
        edges += [{"from": root, "to": instance, "relation": "ConsumedBy"}, {"from": instance, "to": usage, "relation": "MeasuredAs"}]
    return {"apiVersion": f"{GROUP}/{VERSION}", "kind": "BifurcationPath", "metadata": {"name": name, "namespace": ns, "labels": shard["metadata"]["labels"]}, "spec": spec, "status": {"observedGeneration": 1, "nodes": nodes, "edges": edges, "nodeCount": len(nodes), "edgeCount": len(edges), "complete": False, "usage": [], "missingMeters": [x["name"] for x in shard["spec"]["meters"]]}}


class Kubernetes:
    def __init__(self):
        self.base = os.environ.get("KUBERNETES_SERVICE_HOST")
        if not self.base:
            raise RuntimeError("KUBERNETES_SERVICE_HOST is required")
        port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        self.base = f"https://{self.base}:{port}"
        self.token = open("/var/run/secrets/kubernetes.io/serviceaccount/token").read().strip()
        self.ssl = ssl.create_default_context(cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")

    def request(self, method, path, body=None, content_type="application/json"):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={"Authorization": f"Bearer {self.token}", "Content-Type": content_type, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, context=self.ssl, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise RuntimeError(f"Kubernetes API {method} {path}: HTTP {exc.code}") from exc

    def list(self, api, plural):
        prefix = "/api/" if api == "v1" else "/apis/"
        return (self.request("GET", f"{prefix}{api}/{plural}") or {}).get("items", [])

    def apply(self, plural, obj):
        ns, name = obj["metadata"].get("namespace"), obj["metadata"]["name"]
        base = f"/apis/{GROUP}/{VERSION}" + (f"/namespaces/{ns}" if ns else "") + f"/{plural}"
        status = obj.pop("status", None)
        existing = self.request("GET", f"{base}/{name}")
        if existing:
            desired_meta = obj["metadata"]
            current_meta = existing["metadata"]
            unchanged = existing.get("spec") == obj.get("spec") and current_meta.get("labels", {}) == desired_meta.get("labels", {}) and current_meta.get("finalizers", []) == desired_meta.get("finalizers", [])
            if unchanged:
                saved = existing
            else:
                obj["metadata"]["resourceVersion"] = current_meta["resourceVersion"]
                saved = self.request("PUT", f"{base}/{name}", obj)
        else:
            saved = self.request("POST", base, obj)
        if status is not None:
            current_status = existing.get("status", {}) if existing else {}
            current_conditions = {(x.get("type"), x.get("status"), x.get("reason")): x.get("lastTransitionTime") for x in current_status.get("conditions", [])}
            for item in status.get("conditions", []):
                item["lastTransitionTime"] = current_conditions.get((item.get("type"), item.get("status"), item.get("reason")), item["lastTransitionTime"])
            if current_status != status:
                saved = self.request("PATCH", f"{base}/{name}/status", {"status": status}, "application/merge-patch+json")
        return saved

    def delete(self, plural, namespace, name):
        base = f"/apis/{GROUP}/{VERSION}/namespaces/{namespace}/{plural}/{name}"
        return self.request("DELETE", base, {"apiVersion": "v1", "kind": "DeleteOptions", "propagationPolicy": "Background"})

    def patch_status(self, plural, namespace, name, status):
        base = f"/apis/{GROUP}/{VERSION}/namespaces/{namespace}/{plural}/{name}/status"
        return self.request("PATCH", base, {"status": status}, "application/merge-patch+json")


def reconcile(kube, config):
    resources = {"pods": kube.list("v1", "pods"), "services": kube.list("v1", "services"), "httproutes": kube.list("gateway.networking.k8s.io/v1", "httproutes")}
    bindings = kube.list(f"{GROUP}/{VERSION}", "consumptionbindings")
    consumables = {x["metadata"]["name"]: x for x in kube.list(f"{GROUP}/{VERSION}", "consumables")}
    count = 0
    desired = set()
    for api, plural, kind in WORKLOADS:
        for workload in kube.list(api, plural):
            workload["kind"] = kind
            shard = shard_for(workload, resources, config)
            binding = next((x for x in bindings if x["metadata"].get("namespace") == shard["metadata"]["namespace"] and shard["metadata"]["name"] in x.get("spec", {}).get("shardRefs", [])), None)
            desired.add((shard["metadata"]["namespace"], shard["metadata"]["name"]))
            kube.apply("serviceshards", shard)
            kube.apply("bifurcationpaths", path_for(shard, binding))
            count += 1
    for existing in kube.list(f"{GROUP}/{VERSION}", "serviceshards"):
        meta = existing["metadata"]
        key = (meta["namespace"], meta["name"])
        if meta.get("labels", {}).get("app.kubernetes.io/managed-by") != "birfucate-discovery" or key in desired:
            continue
        if FINALIZER in meta.get("finalizers", []):
            kube.request("PATCH", f"/apis/{GROUP}/{VERSION}/namespaces/{key[0]}/serviceshards/{key[1]}", {"metadata": {"finalizers": []}}, "application/merge-patch+json")
        kube.delete("bifurcationpaths", key[0], key[1])
        kube.delete("serviceshards", key[0], key[1])
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for binding in bindings:
        spec, meta = binding.get("spec", {}), binding["metadata"]
        consumable = consumables.get(spec.get("consumableRef"))
        approved = bool(consumable and consumable.get("spec", {}).get("lifecycle") == "Approved")
        resolved = all((meta["namespace"], name) in desired for name in spec.get("shardRefs", []))
        access_ok, access_reason = access_valid(binding, consumable)
        ready = approved and resolved and access_ok
        reason = "Ready" if ready else "OfferingNotApproved" if not approved else "ShardNotFound" if not resolved else access_reason
        status = {"observedGeneration": meta.get("generation", 1), "phase": "Ready" if ready else "Pending", "conditions": [{"type": "Ready", "status": "True" if ready else "False", "reason": reason, "lastTransitionTime": now}]}
        current = binding.get("status", {})
        if current.get("phase") != status["phase"] or [(x.get("type"), x.get("status"), x.get("reason")) for x in current.get("conditions", [])] != [("Ready", status["conditions"][0]["status"], reason)]:
            kube.patch_status("consumptionbindings", meta["namespace"], meta["name"], status)
    return count


def main():
    config = json.load(open(os.environ.get("BIFURCATE_DISCOVERY_CONFIG", "/etc/birfucate/discovery.json")))
    kube = Kubernetes()
    while True:
        try:
            count = reconcile(kube, config)
            print(json.dumps({"event": "reconciled", "workloads": count}), flush=True)
        except Exception as exc:
            print(json.dumps({"event": "reconcile_failed", "error": str(exc)}), flush=True)
        time.sleep(config.get("intervalSeconds", 120))


if __name__ == "__main__":
    main()
