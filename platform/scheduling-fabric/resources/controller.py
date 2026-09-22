#!/usr/bin/env python3
"""Penrose observer/projector. It never binds or evicts Pods."""
import json, os, ssl, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

API = os.getenv("KUBERNETES_SERVICE_HOST", "127.0.0.1")
PORT = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")
BASE = f"https://{API}:{PORT}"
SA = "/var/run/secrets/kubernetes.io/serviceaccount"
TOKEN = open(SA + "/token").read().strip() if os.path.exists(SA + "/token") else "test"
CTX = ssl.create_default_context(cafile=SA + "/ca.crt") if os.path.exists(SA + "/ca.crt") else ssl.create_default_context()
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}
GROUP = "/apis/scheduling.re8ch.com/v1alpha1"
VM = os.getenv("VICTORIA_URL", "http://vmselect-re8ch-metrics.observability-system.svc.cluster.local:8481/select/0/prometheus")
HOLDER = os.getenv("POD_NAME", "penrose-controller")

def now(): return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
def clamp(v): return max(0, min(100, round(v)))
def request(path, method="GET", body=None, content_type="application/json"):
    data = json.dumps(body).encode() if body is not None else None
    headers = dict(HEADERS)
    if data is not None: headers["Content-Type"] = content_type
    attempts = 3 if method in ("GET", "PATCH") else 1
    for attempt in range(attempts):
        req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, context=CTX, timeout=20) as response:
                return json.load(response) if response.length != 0 else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 404: return None
            raise RuntimeError(f"{method} {path}: {exc.code} {exc.read().decode()[:500]}")
        except (OSError, TimeoutError, urllib.error.URLError):
            if attempt + 1 == attempts:
                raise
            time.sleep(0.5 * (attempt + 1))

def items(path):
    value = request(path)
    return value.get("items", []) if value else []

def patch(path, body, status=False):
    return request(path + ("/status" if status else ""), "PATCH", body, "application/merge-patch+json")

def is_leader():
    path = "/apis/coordination.k8s.io/v1/namespaces/scheduling-system/leases/penrose-controller"
    lease = request(path)
    timestamp = now()
    if lease is None:
        body = {"apiVersion": "coordination.k8s.io/v1", "kind": "Lease", "metadata": {"name": "penrose-controller"},
                "spec": {"holderIdentity": HOLDER, "leaseDurationSeconds": 45, "acquireTime": timestamp, "renewTime": timestamp}}
        try: request("/apis/coordination.k8s.io/v1/namespaces/scheduling-system/leases", "POST", body)
        except RuntimeError: return False
        return True
    spec = lease.get("spec", {})
    renewal = spec.get("renewTime") or spec.get("acquireTime")
    expired = not renewal or (datetime.now(timezone.utc) - datetime.fromisoformat(renewal.replace("Z", "+00:00"))).total_seconds() > spec.get("leaseDurationSeconds", 45)
    if spec.get("holderIdentity") != HOLDER and not expired: return False
    patch(path, {"spec": {"holderIdentity": HOLDER, "leaseDurationSeconds": 45, "renewTime": timestamp}})
    return True

def labels_match(labels, selector): return all(labels.get(k) == v for k, v in (selector or {}).items())

NODE_FAILURE_REASONS = {"NodeNotReady", "FreeDiskSpaceFailed", "ImageGCFailed", "ContainerGCFailed"}

def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

def node_reliability(node, events, spec, measured_at=None):
    """Build a durable scheduling gate from Node state and recent history."""
    measured_at = measured_at or datetime.now(timezone.utc)
    conditions = {item.get("type"): item for item in node.get("status", {}).get("conditions", [])}
    ready_condition = conditions.get("Ready", {})
    ready_since = parse_time(ready_condition.get("lastTransitionTime"))
    ready_seconds = max(0, int((measured_at - ready_since).total_seconds())) if ready_since else 0
    pressure = sorted(name for name in ("DiskPressure", "MemoryPressure", "PIDPressure", "NetworkUnavailable")
                      if conditions.get(name, {}).get("status") == "True")
    window_seconds = int(spec.get("reliability", {}).get("historyWindowSeconds", 86400))
    cutoff = measured_at - timedelta(seconds=window_seconds)
    node_name = node.get("metadata", {}).get("name")
    failures = []
    for event in events:
        if event.get("involvedObject", {}).get("name") != node_name or event.get("reason") not in NODE_FAILURE_REASONS:
            continue
        observed = parse_time(event.get("eventTime") or event.get("lastTimestamp") or event.get("metadata", {}).get("creationTimestamp"))
        if observed and observed >= cutoff:
            failures.append(event.get("reason"))
    not_ready_count = failures.count("NodeNotReady")
    minimum_ready = int(spec.get("reliability", {}).get("minimumReadySeconds", 1800))
    max_not_ready = int(spec.get("reliability", {}).get("maxNotReadyEvents", 0))
    currently_ready = ready_condition.get("status") == "True"
    eligible = (currently_ready and ready_seconds >= minimum_ready and not pressure
                and not node.get("spec", {}).get("unschedulable", False) and not_ready_count <= max_not_ready
                and not spec.get("reliability", {}).get("maintenanceMode", False))
    risk = int(spec.get("reliability", {}).get("disruptionRisk", 50))
    score = clamp(100 - risk - min(50, not_ready_count * 20) - min(20, len(failures) * 3)
                  - (40 if pressure else 0) - (40 if not currently_ready else 0))
    return {"eligible": eligible, "score": score, "readySeconds": ready_seconds,
            "recentFailures": len(failures), "recentNotReady": not_ready_count, "pressure": pressure}

def profile_labels(spec, target):
    labels = {"scheduling.re8ch.com/provider": spec.get("background", {}).get("provider", "unknown"),
              "scheduling.re8ch.com/environment": spec.get("background", {}).get("environment", "unknown"),
              "scheduling.re8ch.com/reliability-class": spec.get("reliability", {}).get("class", "unknown"),
              "scheduling.re8ch.com/node-profile": spec.get("reservationProfile", "worker-small"),
              "scheduling.re8ch.com/failure-domain": spec.get("failureDomains", {}).get("host", target)}
    service_plane = spec.get("capabilities", {}).get("servicePlane")
    if service_plane:
        labels["scheduling.re8ch.com/service-plane"] = service_plane
    background = spec.get("background", {})
    labels["scheduling.re8ch.com/cost-class"] = background.get("costClass", "unknown")
    capabilities = spec.get("capabilities", {})
    for storage in capabilities.get("storage", []):
        labels[f"scheduling.re8ch.com/capability-storage-{storage}"] = "true"
    if capabilities.get("gpu"):
        labels["scheduling.re8ch.com/capability-gpu"] = str(capabilities["gpu"])
    for key in ("publicEdge", "egress", "trafficQuotaAware"):
        if capabilities.get(key): labels[f"scheduling.re8ch.com/capability-{key.replace('E', '-e').lower()}"] = "true"
    # These are eligibility classes, never a replacement for native hard
    # locality constraints such as node-local disks or Gateway backends.
    reliable = spec.get("reliability", {}).get("class") in ("anchor", "stable")
    labels["scheduling.re8ch.com/placement-general-reliable"] = "true" if reliable else "false"
    labels["scheduling.re8ch.com/placement-batch-opportunistic"] = "true" if capabilities.get("scheduling") else "false"
    return labels

def placement_eligible_nodes(placement, nodes):
    spec = placement.get("spec", {})
    selector = spec.get("nodeSelector", {})
    eligible = [node for node in nodes if labels_match(node.get("metadata", {}).get("labels", {}), selector)]
    eligible = [node for node in eligible if node.get("metadata", {}).get("labels", {}).get("scheduling.re8ch.com/capacity-window") == "open"]
    if not spec.get("allowControlPlane", False):
        eligible = [node for node in eligible if node.get("metadata", {}).get("labels", {}).get("node-role.kubernetes.io/control-plane") != "true"]
    return eligible

def reconcile_placement_classes(nodes):
    for placement in items(GROUP + "/placementclasses"):
        eligible = placement_eligible_nodes(placement, nodes)
        domains = {node.get("metadata", {}).get("labels", {}).get(placement.get("spec", {}).get("topologyKey")) for node in eligible}
        ready = bool(eligible)
        condition = {"type": "Ready", "status": "True" if ready else "False",
                     "reason": "EligibleNodes" if ready else "NoEligibleNodes",
                     "message": f"{len(eligible)} eligible nodes across {len(domains - {None})} failure domains", "lastTransitionTime": now()}
        patch(f"{GROUP}/placementclasses/{placement['metadata']['name']}",
              {"status": {"observedGeneration": placement["metadata"].get("generation"), "eligibleNodes": len(eligible),
                          "failureDomains": len(domains - {None}), "conditions": [condition]}}, True)

def reconcile_placement_admission_checks(nodes):
    """Mark Kueue placement checks Ready only when a named class is usable.

    PodSet updates are additive, as required by Kueue; an invalid or unavailable
    class is explicitly rejected instead of silently falling back to a node.
    """
    classes = {x["metadata"]["name"]: x for x in items(GROUP + "/placementclasses")}
    for workload in items("/apis/kueue.x-k8s.io/v1beta2/workloads"):
        checks = workload.get("status", {}).get("admissionChecks", [])
        if not any(check.get("name") == "re8ch-placement" for check in checks): continue
        podsets = workload.get("spec", {}).get("podSets", [])
        annotation = next((p.get("template", {}).get("metadata", {}).get("annotations", {}).get("scheduling.re8ch.com/placement-class") for p in podsets if p.get("template", {}).get("metadata", {}).get("annotations", {}).get("scheduling.re8ch.com/placement-class")), None)
        placement = classes.get(annotation)
        eligible = placement_eligible_nodes(placement, nodes) if placement else []
        state = "Ready" if eligible else ("Pending" if placement else "Rejected")
        message = (f"PlacementClass {annotation} is ready" if eligible else
                   f"PlacementClass {annotation} is waiting for node capacity" if placement else
                   f"PlacementClass {annotation or '<missing>'} does not exist")
        update = {"name": "re8ch-placement", "state": state, "message": message, "lastTransitionTime": now()}
        if eligible:
            update["podSetUpdates"] = [{"name": podset["name"], "nodeSelector": placement["spec"]["nodeSelector"]} for podset in podsets]
        remaining = [check for check in checks if check.get("name") != "re8ch-placement"]
        patch(f"/apis/kueue.x-k8s.io/v1beta2/namespaces/{workload['metadata']['namespace']}/workloads/{workload['metadata']['name']}", {"status": {"admissionChecks": remaining + [update]}}, True)

def reconcile_placement_admission_check():
    check = request("/apis/kueue.x-k8s.io/v1beta2/admissionchecks/re8ch-placement")
    if check:
        patch("/apis/kueue.x-k8s.io/v1beta2/admissionchecks/re8ch-placement", {"status": {"conditions": [{"type": "Active", "status": "True", "reason": "ControllerReady", "message": "Placement admission controller is leader-elected and available", "lastTransitionTime": now()}]}}, True)

def cpu(value="0"):
    text = str(value)
    if text.endswith("n"): return float(text[:-1]) / 1_000_000_000
    if text.endswith("u"): return float(text[:-1]) / 1_000_000
    if text.endswith("m"): return float(text[:-1]) / 1000
    return float(text or 0)

def memory(value="0"):
    text = str(value)
    units = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4}
    for suffix, factor in units.items():
        if text.endswith(suffix): return float(text[:-len(suffix)]) * factor
    return float(text or 0)

def trimaran_nodes():
    series = []
    try:
        for query in ("instance:node_cpu_utilisation:rate:sum", "instance:node_memory_utilisation:ratio"):
            url = VM + "/api/v1/query?" + urllib.parse.urlencode({"query": query})
            with urllib.request.urlopen(url, timeout=10) as response:
                series.append({x.get("metric", {}).get("instance") or x.get("metric", {}).get("node") for x in json.load(response).get("data", {}).get("result", [])})
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
        return set()
    return set.intersection(*series) if series else set()

CAPACITY_TAINT = {"key": "scheduling.re8ch.com/capacity-window", "value": "closed", "effect": "NoSchedule"}
CAPACITY_PATHS = ("/", "/var/lib/kubelet", "/var/lib/containerd", "/var/lib/rancher/k3s/agent/containerd")

def metric_rows(query, max_age=300):
    """Return only recent samples; a missing series is never free capacity."""
    url = VM + "/api/v1/query?" + urllib.parse.urlencode({"query": query})
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.load(response)
    if payload.get("status") != "success":
        raise RuntimeError("capacity metric query failed")
    measured = time.time()
    rows = []
    for row in payload.get("data", {}).get("result", []):
        try:
            timestamp, value = float(row["value"][0]), float(row["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if 0 <= measured - timestamp <= max_age and value >= 0 and value != float("inf"):
            rows.append((row.get("metric", {}), value))
    return rows

def capacity_measurements():
    names = {
        "size": "node_filesystem_size_bytes", "available": "node_filesystem_avail_bytes",
        "files": "node_filesystem_files", "filesFree": "node_filesystem_files_free",
        "readonly": "node_filesystem_readonly", "deviceError": "node_filesystem_device_error",
        "memoryTotal": "node_memory_MemTotal_bytes", "memoryAvailable": "node_memory_MemAvailable_bytes",
    }
    result = {}
    for field, metric_name in names.items():
        for labels, value in metric_rows(f'{metric_name}{{job="re8ch-node-storage-exporter"}}'):
            node = labels.get("node")
            if not node:
                continue
            if field.startswith("memory"):
                result.setdefault(node, {})[field] = value
            else:
                identity = (labels.get("mountpoint"), labels.get("device"), labels.get("fstype"))
                result.setdefault(node, {}).setdefault("mounts", {}).setdefault(identity, {})[field] = value
    for labels, value in metric_rows("instance:node_cpu_utilisation:rate:sum"):
        node = labels.get("node") or labels.get("instance")
        if node:
            result.setdefault(node, {})["cpuRatio"] = value
    return result

def capacity_window(node, measurement, reservations=None, previously_closed=False):
    """Choose a new-allocation window from host headroom and Kubernetes limits."""
    conditions = {c.get("type"): c.get("status") for c in node.get("status", {}).get("conditions", [])}
    if conditions.get("Ready") != "True":
        return "unknown", ["node-not-ready"]
    mounts = measurement.get("mounts", {})
    relevant = {}
    for path in CAPACITY_PATHS:
        candidates = [(identity, values) for identity, values in mounts.items()
                      if identity[0] and (path == identity[0] or path.startswith(identity[0].rstrip("/") + "/"))]
        if candidates:
            identity, values = max(candidates, key=lambda entry: len(entry[0][0]))
            relevant[identity] = values
    if not relevant or "memoryTotal" not in measurement or "memoryAvailable" not in measurement or "cpuRatio" not in measurement:
        return "unknown", ["capacity-telemetry-missing"]
    reasons = []
    for identity, values in relevant.items():
        size, available = values.get("size", 0), values.get("available")
        if size <= 0 or available is None or "readonly" not in values or "deviceError" not in values:
            return "unknown", ["filesystem-telemetry-incomplete"]
        if values["readonly"] or values["deviceError"]:
            reasons.append("filesystem-error:" + identity[0])
        limit = max(size * (0.20 if previously_closed else 0.15), (10 if previously_closed else 5) * 1024**3)
        if available < limit:
            reasons.append("filesystem-low:" + identity[0])
        files, free = values.get("files"), values.get("filesFree")
        if files and free is not None and free / files < (0.20 if previously_closed else 0.15):
            reasons.append("inodes-low:" + identity[0])
    memory_total, memory_available = measurement["memoryTotal"], measurement["memoryAvailable"]
    if memory_total <= 0:
        return "unknown", ["memory-telemetry-incomplete"]
    if memory_available < max(memory_total * (0.20 if previously_closed else 0.10), (1 if previously_closed else 0.5) * 1024**3):
        reasons.append("memory-low")
    if measurement["cpuRatio"] > (0.80 if previously_closed else 0.95):
        reasons.append("cpu-saturated")
    allocatable = node.get("status", {}).get("allocatable", {})
    reservations = reservations or {}
    for resource, threshold in (("cpu", 0.80 if previously_closed else 0.90),
                                ("memory", 0.80 if previously_closed else 0.90),
                                ("ephemeral-storage", 0.75 if previously_closed else 0.90),
                                ("pods", 0.85 if previously_closed else 0.95)):
        parser = cpu if resource == "cpu" else memory
        limit = parser(allocatable.get(resource, "0"))
        if limit > 0 and reservations.get(resource, 0) / limit >= threshold:
            reasons.append("requests-high:" + resource)
    for pressure in ("DiskPressure", "MemoryPressure", "PIDPressure"):
        if conditions.get(pressure) == "True":
            reasons.append(pressure)
    return ("closed" if reasons else "open"), reasons

def reconcile_capacity_windows(nodes):
    try:
        measurements = capacity_measurements()
    except (OSError, TimeoutError, urllib.error.URLError, ValueError, RuntimeError) as exc:
        print(f"capacity telemetry unavailable: {exc}", flush=True)
        return
    reservations = {}
    for pod in items("/api/v1/pods"):
        node_name = pod.get("spec", {}).get("nodeName")
        if not node_name or pod.get("status", {}).get("phase") in ("Succeeded", "Failed"):
            continue
        booked = reservations.setdefault(node_name, {"cpu": 0, "memory": 0, "ephemeral-storage": 0, "pods": 0})
        booked["pods"] += 1
        for resource in ("cpu", "memory", "ephemeral-storage"):
            parser = cpu if resource == "cpu" else memory
            containers = pod.get("spec", {}).get("containers", [])
            init_containers = pod.get("spec", {}).get("initContainers", [])
            normal = sum(parser(c.get("resources", {}).get("requests", {}).get(resource, "0")) for c in containers)
            init = max((parser(c.get("resources", {}).get("requests", {}).get(resource, "0")) for c in init_containers), default=0)
            overhead = parser(pod.get("spec", {}).get("overhead", {}).get(resource, "0"))
            booked[resource] += max(normal, init) + overhead
    for node in nodes:
        metadata = node.get("metadata", {})
        name = metadata.get("name")
        if not name:
            continue
        taints = node.get("spec", {}).get("taints", [])
        owned = any(t.get("key") == CAPACITY_TAINT["key"] for t in taints)
        state, reasons = capacity_window(node, measurements.get(name, {}), reservations.get(name), owned)
        labels = {"scheduling.re8ch.com/capacity-window": state}
        annotations = {"scheduling.re8ch.com/capacity-reasons": ",".join(reasons)[:240]}
        ever_observed = metadata.get("annotations", {}).get("scheduling.re8ch.com/capacity-ever-observed") == "true"
        if state != "unknown":
            annotations["scheduling.re8ch.com/capacity-ever-observed"] = "true"
        if (metadata.get("labels", {}).get("scheduling.re8ch.com/capacity-window") != state
                or any(metadata.get("annotations", {}).get(key) != value for key, value in annotations.items())):
            updated = patch(f"/api/v1/nodes/{name}", {"metadata": {"labels": labels, "annotations": annotations}})
            metadata["resourceVersion"] = updated["metadata"]["resourceVersion"]
        metadata.setdefault("labels", {}).update(labels)
        # Bootstrap waits for the first complete sample. Once observed, stale
        # telemetry closes the window until a fresh open sample returns.
        desired_closed = state == "closed" or (state == "unknown" and (ever_observed or owned))
        if desired_closed == owned:
            continue
        new_taints = [t for t in taints if t.get("key") != CAPACITY_TAINT["key"]]
        if desired_closed:
            new_taints.append(CAPACITY_TAINT)
        try:
            request(f"/api/v1/nodes/{name}", "PATCH", [
                {"op": "test", "path": "/metadata/resourceVersion", "value": metadata["resourceVersion"]},
                {"op": "add", "path": "/spec/taints", "value": new_taints},
            ], "application/json-patch+json")
        except RuntimeError as exc:
            print(f"capacity window taint update deferred for {name}: {exc}", flush=True)

def owner_workload(pod, replicasets, deployments, statefulsets):
    owners = pod.get("metadata", {}).get("ownerReferences", [])
    for owner in owners:
        if owner.get("kind") == "StatefulSet":
            return next((x for x in statefulsets if x["metadata"]["name"] == owner["name"]), None)
        if owner.get("kind") == "ReplicaSet":
            rs = next((x for x in replicasets if x["metadata"]["name"] == owner["name"]), None)
            dep_name = next((x["name"] for x in (rs or {}).get("metadata", {}).get("ownerReferences", []) if x.get("kind") == "Deployment"), None)
            return next((x for x in deployments if x["metadata"]["name"] == dep_name), None)
    return None

def score_service(service, slices, pods, pod_metrics, nodes, replicasets, deployments, statefulsets):
    namespace, name = service["metadata"]["namespace"], service["metadata"]["name"]
    selector = service.get("spec", {}).get("selector") or {}
    members = [p for p in pods if labels_match(p.get("metadata", {}).get("labels", {}), selector)]
    workloads = [owner_workload(p, replicasets, deployments, statefulsets) for p in members]
    workloads = [w for w in workloads if w]
    desired = max([w.get("spec", {}).get("replicas", 0) for w in workloads] or [0])
    endpoints = [e for s in slices if s.get("metadata", {}).get("labels", {}).get("kubernetes.io/service-name") == name for e in s.get("endpoints", [])]
    ready = sum(1 for e in endpoints if e.get("conditions", {}).get("ready") is True)
    expected = max(desired, len(endpoints), 1)
    availability = min(1, ready / expected)
    node_by_name = {n["metadata"]["name"]: n for n in nodes}
    assigned = [p for p in members if p.get("spec", {}).get("nodeName")]
    domains = {node_by_name.get(p["spec"]["nodeName"], {}).get("metadata", {}).get("labels", {}).get("scheduling.re8ch.com/failure-domain", p["spec"]["nodeName"]) for p in assigned}
    domain_target = max(1, min(desired, 3))
    restarts = sum(s.get("restartCount", 0) for p in members for s in p.get("status", {}).get("containerStatuses", []))
    pending = sum(1 for p in members if p.get("status", {}).get("phase") == "Pending")
    schedulers = sorted({p.get("spec", {}).get("schedulerName", "default-scheduler") for p in members})
    metrics_by_pod = {x["metadata"]["name"]: x for x in pod_metrics}
    requested_cpu = sum(cpu(c.get("resources", {}).get("requests", {}).get("cpu", "0")) for p in members for c in p.get("spec", {}).get("containers", []))
    requested_memory = sum(memory(c.get("resources", {}).get("requests", {}).get("memory", "0")) for p in members for c in p.get("spec", {}).get("containers", []))
    used_cpu = sum(cpu(c.get("usage", {}).get("cpu", "0")) for p in members for c in metrics_by_pod.get(p["metadata"]["name"], {}).get("containers", []))
    used_memory = sum(memory(c.get("usage", {}).get("memory", "0")) for p in members for c in metrics_by_pod.get(p["metadata"]["name"], {}).get("containers", []))
    metrics_present = bool(members and all(p["metadata"]["name"] in metrics_by_pod for p in members))
    cpu_ratio = min(1, used_cpu / requested_cpu) if requested_cpu else 0
    memory_ratio = min(1, used_memory / requested_memory) if requested_memory else 0
    efficiency = clamp(100 * ((cpu_ratio + memory_ratio) / 2)) if metrics_present and requested_cpu and requested_memory else None
    evidence = {"endpointAvailability": availability, "failureDomains": len(domains), "desiredFailureDomains": domain_target,
                "restarts": restarts, "pending": pending, "readyEndpoints": ready, "expectedEndpoints": expected}
    resilience = clamp(100 * (0.62 * availability + 0.38 * min(1, len(domains) / domain_target)))
    stability = clamp(100 * availability - min(35, restarts * 3) - min(25, pending * 8))
    completeness = round(0.65 + (0.20 if efficiency is not None else 0), 2)
    missing = ["opencost.unit-cost", "cilium.network-efficiency", "storage-io"]
    if efficiency is None: missing.insert(0, "metrics.k8s.io.pod-usage")
    return {"cohort": schedulers[0] if len(schedulers) == 1 else "mixed", "desiredReplicas": desired,
            "eligible": bool(selector and desired > 0 and endpoints), "observation": {"measuredAt": now(), "window": "15m",
            "scores": {"resilience": resilience, "efficiency": efficiency, "stability": stability}, "completeness": completeness,
            "confidence": completeness, "complete": completeness >= 0.8 and efficiency is not None, "pareto": False, "missingEvidence": missing, "evidence": evidence}}

def reconcile_profiles(nodes, events):
    node_names = {n["metadata"]["name"] for n in nodes}
    metric_nodes = trimaran_nodes()
    for node_name in node_names:
        patch(f"/api/v1/nodes/{node_name}", {"metadata": {"labels": {"scheduling.re8ch.com/trimaran-ready": "true" if node_name in metric_nodes else "false"}}})
    for profile in items(GROUP + "/nodeprofiles"):
        spec, name = profile.get("spec", {}), profile["metadata"]["name"]
        target = spec.get("nodeName")
        labels = profile_labels(spec, target)
        targets = [x for x in [target] + spec.get("aliases", []) if x in node_names]
        for node_name in targets:
            node = next(x for x in nodes if x["metadata"]["name"] == node_name)
            reliability = node_reliability(node, events, spec)
            projected = dict(labels)
            projected["scheduling.re8ch.com/failure-domain"] = spec.get("failureDomains", {}).get("host", node_name)
            projected["scheduling.re8ch.com/reliability-score"] = str(reliability["score"])
            projected["scheduling.re8ch.com/service-plane-eligible"] = "true" if reliability["eligible"] else "false"
            if spec.get("capabilities", {}).get("servicePlane") and not reliability["eligible"]:
                projected["scheduling.re8ch.com/service-plane"] = None
            elif not spec.get("capabilities", {}).get("servicePlane"):
                projected["scheduling.re8ch.com/service-plane"] = None
            patch(f"/api/v1/nodes/{node_name}", {"metadata": {"labels": projected}})
        condition = {"type": "Ready", "status": "True" if targets else "False", "reason": "Projected" if targets else "NodeMissing", "lastTransitionTime": now()}
        patch(f"{GROUP}/nodeprofiles/{name}", {"status": {"observedGeneration": profile["metadata"].get("generation"), "projectedNodes": targets, "projectedLabels": labels, "conditions": [condition]}}, True)

def reconcile_scheduler_profiles(nodes):
    ready = [n for n in nodes if any(c.get("type") == "Ready" and c.get("status") == "True" for c in n.get("status", {}).get("conditions", []))]
    trimaran = [n for n in ready if n.get("metadata", {}).get("labels", {}).get("scheduling.re8ch.com/trimaran-ready") == "true"]
    for profile in items(GROUP + "/schedulerprofiles"):
        spec, name = profile.get("spec", {}), profile["metadata"]["name"]
        coverage = 1 if spec.get("schedulerName") == "default-scheduler" else (len(trimaran) / len(ready) if ready else 0)
        valid = all(p["bounds"]["minimum"] <= p["weight"] <= p["bounds"]["maximum"] for p in spec.get("plugins", []))
        ready_state = valid and coverage >= spec.get("minimumMetricsCoverage", 1)
        patch(f"{GROUP}/schedulerprofiles/{name}", {"status": {"observedGeneration": profile["metadata"].get("generation"), "metricsCoverage": round(coverage, 3),
              "eligibleNodes": len(ready) if spec.get("schedulerName") == "default-scheduler" else len(trimaran), "revision": profile["metadata"].get("resourceVersion"),
              "conditions": [{"type": "Ready", "status": "True" if ready_state else "False", "reason": "Validated" if ready_state else "CoverageOrBoundsFailed", "lastTransitionTime": now()}]}}, True)

def reconcile_services(nodes):
    namespaces = items("/api/v1/namespaces")
    for ns in [x["metadata"]["name"] for x in namespaces]:
        services = items(f"/api/v1/namespaces/{ns}/services")
        slices = items(f"/apis/discovery.k8s.io/v1/namespaces/{ns}/endpointslices")
        pods = items(f"/api/v1/namespaces/{ns}/pods")
        pod_metrics = items(f"/apis/metrics.k8s.io/v1beta1/namespaces/{ns}/pods")
        replicasets = items(f"/apis/apps/v1/namespaces/{ns}/replicasets")
        deployments = items(f"/apis/apps/v1/namespaces/{ns}/deployments")
        statefulsets = items(f"/apis/apps/v1/namespaces/{ns}/statefulsets")
        existing = {x.get("spec", {}).get("serviceRef", {}).get("name"): x for x in items(f"{GROUP}/namespaces/{ns}/serviceoutcomepolicies")}
        for service in services:
            result = score_service(service, slices, pods, pod_metrics, nodes, replicasets, deployments, statefulsets)
            name = service["metadata"]["name"]
            policy = existing.get(name)
            if not policy and result["eligible"]:
                body = {"apiVersion": "scheduling.re8ch.com/v1alpha1", "kind": "ServiceOutcomePolicy", "metadata": {"name": name},
                        "spec": {"serviceRef": {"name": name}, "enrollment": "Auto", "comparisonGroup": name,
                        "desired": {"resilience": 80, "efficiency": 70, "stability": 80},
                        "observation": {"currentWindow": "15m", "baselineWindow": "24h", "minimumCompleteness": 0.8}}}
                policy = request(f"{GROUP}/namespaces/{ns}/serviceoutcomepolicies", "POST", body)
            if policy and policy.get("spec", {}).get("enrollment") != "Disabled":
                patch(f"{GROUP}/namespaces/{ns}/serviceoutcomepolicies/{policy['metadata']['name']}", {"status": result}, True)

def reconcile_disturbances(nodes):
    namespace = "scheduling-system"
    existing = items(f"{GROUP}/namespaces/{namespace}/disturbancewindows")
    active = {x.get("spec", {}).get("summary"): x for x in existing if not x.get("spec", {}).get("endedAt")}
    for node in nodes:
        ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in node.get("status", {}).get("conditions", []))
        summary = f"Node {node['metadata']['name']} NotReady"
        if not ready and summary not in active:
            body = {"apiVersion": "scheduling.re8ch.com/v1alpha1", "kind": "DisturbanceWindow", "metadata": {"generateName": "node-not-ready-"},
                    "spec": {"source": "NodeNotReady", "startedAt": now(), "summary": summary}}
            request(f"{GROUP}/namespaces/{namespace}/disturbancewindows", "POST", body)
        if ready and summary in active:
            patch(f"{GROUP}/namespaces/{namespace}/disturbancewindows/{active[summary]['metadata']['name']}", {"spec": {"endedAt": now()}, "status": {"phase": "settled"}})

def reconcile_operations():
    scheduler_profiles = {x.get("spec", {}).get("schedulerName"): x for x in items(GROUP + "/schedulerprofiles")}
    for namespace in [x["metadata"]["name"] for x in items("/api/v1/namespaces")]:
        for operation in items(f"{GROUP}/namespaces/{namespace}/penroseoperations"):
            name, spec = operation["metadata"]["name"], operation.get("spec", {})
            if operation.get("status", {}).get("phase") in ("Applied", "Failed"): continue
            if not spec.get("approved"):
                patch(f"{GROUP}/namespaces/{namespace}/penroseoperations/{name}", {"status": {"phase": "AwaitingApproval", "observedAt": now()}}, True)
                continue
            if spec.get("action") != "SwitchScheduler":
                patch(f"{GROUP}/namespaces/{namespace}/penroseoperations/{name}", {"status": {"phase": "Failed", "reason": "UnsupportedAction", "observedAt": now()}}, True)
                continue
            target, preview = spec.get("targetRef", {}), spec.get("preview", {})
            scheduler = next((x.get("value") for x in preview.get("patch", []) if x.get("path") == "/spec/template/spec/schedulerName"), None)
            profile = scheduler_profiles.get(scheduler)
            if target.get("kind") != "Deployment" or not profile or profile.get("status", {}).get("conditions", [{}])[0].get("status") != "True":
                patch(f"{GROUP}/namespaces/{namespace}/penroseoperations/{name}", {"status": {"phase": "Failed", "reason": "TargetOrSchedulerNotReady", "observedAt": now()}}, True)
                continue
            node_selector = profile.get("spec", {}).get("eligibleNodeSelector", {})
            body = {"spec": {"template": {"metadata": {"annotations": {"scheduling.re8ch.com/operation": name}}, "spec": {"schedulerName": scheduler, "nodeSelector": node_selector}}}}
            result = patch(f"/apis/apps/v1/namespaces/{target.get('namespace', namespace)}/deployments/{target.get('name')}", body)
            patch(f"{GROUP}/namespaces/{namespace}/penroseoperations/{name}", {"status": {"phase": "Applied", "appliedRevision": result.get("metadata", {}).get("resourceVersion"), "observedAt": now()}}, True)

def run():
    while True:
        try:
            if not is_leader():
                time.sleep(15)
                continue
            nodes = items("/api/v1/nodes")
            events = items("/api/v1/events?fieldSelector=involvedObject.kind%3DNode")
            reconcile_capacity_windows(nodes)
            reconcile_profiles(nodes, events)
            reconcile_placement_classes(nodes)
            reconcile_placement_admission_check()
            reconcile_placement_admission_checks(nodes)
            reconcile_scheduler_profiles(nodes)
            reconcile_services(nodes)
            reconcile_disturbances(nodes)
            reconcile_operations()
        except Exception as exc: print(f"reconcile failed: {exc}", flush=True)
        time.sleep(int(os.getenv("RECONCILE_SECONDS", "60")))

if __name__ == "__main__": run()
