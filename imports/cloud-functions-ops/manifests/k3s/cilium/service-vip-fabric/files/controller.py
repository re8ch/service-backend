#!/usr/bin/env python3
"""Observe-first Kubernetes reconciler for Advanced Fabric."""

import json
import hashlib
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import math


HOST = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
PORT = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
BASE = f"https://{HOST}:{PORT}"
TOKEN = open("/var/run/secrets/kubernetes.io/serviceaccount/token", encoding="utf-8").read().strip()
CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
CONTEXT = ssl.create_default_context(cafile=CA)
ADVISOR_URL = os.environ.get("ADVISOR_URL", "http://re8ch-routing-advisor.qianwen-ops.svc.cluster.local:9790")
PATRONI_MEMBERS = json.loads(os.environ.get("PATRONI_MEMBERS_JSON", "{}"))
PG_NAMESPACE = os.environ.get("PG_ROLE_NAMESPACE", "pigsty-external")


def request(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/merge-patch+json" if method == "PATCH" else "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, context=CONTEXT, timeout=15) as response:
        return json.load(response)


def selector_matches(selector, labels):
    return all(labels.get(key) == value for key, value in selector.get("matchLabels", {}).items())


def policy_key(policy):
    spec, meta = policy.get("spec", {}), policy.get("metadata", {})
    return (-int(spec.get("priority", 100)), meta.get("name", ""))


def condition(kind, status, reason, message):
    return {"type": kind, "status": "True" if status else "False", "reason": reason,
            "message": message, "lastTransitionTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def advisor_items(path):
    try:
        with urllib.request.urlopen(ADVISOR_URL + path, timeout=5) as response:
            return json.load(response).get("items", [])
    except Exception:
        return []


def patroni_role_authority():
    """Mirror Patroni's result; never infer or elect a PostgreSQL leader here."""
    cluster = None
    source = None
    for name, address in PATRONI_MEMBERS.items():
        try:
            with urllib.request.urlopen(f"http://{address}:8008/cluster", timeout=4) as response:
                candidate = json.load(response)
            if candidate.get("scope") == "pg-re8ch" and candidate.get("members"):
                cluster, source = candidate, name
                break
        except Exception as exc:
            print(json.dumps({"event": "patroni-observation-failed", "member": name,
                              "address": address, "error": str(exc)}), flush=True)
    if not cluster:
        raise RuntimeError("no Patroni /cluster authority is reachable")
    observed = [{"name": item.get("name"), "address": item.get("host"),
                 "role": item.get("role"), "state": item.get("state"),
                 "timeline": item.get("timeline"), "lag": item.get("lag")}
                for item in cluster["members"]]
    primaries = [item for item in observed if item["role"] in ("primary", "leader") and item["state"] == "running"]
    timelines = {item["timeline"] for item in observed if item["timeline"] is not None}
    if len(observed) < 2 or len(primaries) != 1 or len(timelines) != 1:
        raise RuntimeError(f"Patroni authority ambiguous: observed={len(observed)} primaries={len(primaries)} timelines={len(timelines)}")
    primary = primaries[0]
    replicas = [item for item in observed if item["name"] != primary["name"] and
                item["role"] in ("replica", "standby") and item["state"] in ("running", "streaming")]
    primary_endpoints = [{"addresses": [primary["address"]], "conditions": {"ready": True}}]
    replica_endpoints = [{"addresses": [item["address"]], "conditions": {"ready": True}} for item in replicas]
    for name, endpoints in (("pg-re8ch-primary", primary_endpoints),
                            ("pg-re8ch-replicas", replica_endpoints),
                            ("pgwrite", primary_endpoints)):
        request("PATCH", f"/apis/discovery.k8s.io/v1/namespaces/{PG_NAMESPACE}/endpointslices/{name}",
                {"metadata": {"annotations": {"networking.re8ch.com/role-authority": "patroni"}},
                 "endpoints": endpoints})
    authority = {"schemaVersion": "networking.re8ch.com/v1alpha1", "authority": "patroni",
                 "cluster": "pg-re8ch", "leader": primary, "replicas": replicas,
                 "observedMembers": observed, "sourceMember": source,
                 "observedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    configmap = {"apiVersion": "v1", "kind": "ConfigMap",
                 "metadata": {"name": "pg-re8ch-role-authority", "namespace": "kube-system",
                              "labels": {"app.kubernetes.io/part-of": "re8ch-advanced-fabric",
                                         "networking.re8ch.com/database-role-authority": "patroni"}},
                 "data": {"authority.json": json.dumps(authority, separators=(",", ":"))}}
    try:
        request("PATCH", "/api/v1/namespaces/kube-system/configmaps/pg-re8ch-role-authority", configmap)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        request("POST", "/api/v1/namespaces/kube-system/configmaps", configmap)
    return authority


def quota_pressure(usage, limit):
    if limit in (None, 0):
        return {"ratio": 0.0, "tier": "unlimited", "penalty": 0.0}
    ratio = max(0.0, float(usage) / float(limit))
    if ratio >= 1: return {"ratio": ratio, "tier": "exhausted", "penalty": 1000.0}
    if ratio >= .95: return {"ratio": ratio, "tier": "critical", "penalty": 400.0}
    if ratio >= .80: return {"ratio": ratio, "tier": "warning", "penalty": 120.0}
    return {"ratio": ratio, "tier": "normal", "penalty": -100.0 * ratio}


def rank_paths(profile, node, edges, costs, ready):
    cost_index = {(item.get("source"), item.get("destination"), item.get("pathType")): item for item in costs}
    ranked = []
    for edge in edges:
        if edge.get("source") != node["name"]:
            continue
        peer = edge.get("target")
        if not ready.get(peer, False) or edge.get("bgpUp") != 1 or edge.get("bfdUp") != 1:
            continue
        loss, rtt = float(edge.get("lossRatio") or 0), float(edge.get("rttMs") or 10000)
        if loss > .05:
            continue
        cost = cost_index.get((node["name"], peer, edge.get("pathType")), {})
        pressure = quota_pressure(cost.get("usageGiB", 0), node.get("monthlyTrafficLimitGiB"))
        capacity = max(.001, float(node.get("uplinkMbps", 1)))
        if profile == "fastest": score = rtt + 4000 * loss - 2 * math.log2(capacity)
        elif profile == "greedy": score = pressure["penalty"] + .25 * rtt + 1000 * loss - math.log2(capacity)
        else: score = .6 * rtt + 2500 * loss + .5 * pressure["penalty"] - 1.5 * math.log2(capacity)
        ranked.append({"peer": peer, "pathType": edge.get("pathType"), "score": round(score, 4),
                       "quotaPressure": pressure, "priceStatus": cost.get("priceStatus", "unknown")})
    return sorted(ranked, key=lambda item: (item["score"], item["peer"]))


def make_api_transaction(node, api, operations, guarded):
    """Build the only host-mutation contract and bind it to a stable digest."""
    spec = {
        "version": 1,
        "node": node,
        "guarded": bool(guarded),
        "vip": api.get("vip", ""),
        "vipInterface": operations.get("vipInterface", "lo"),
        "localAsn": operations.get("localAsn"),
        "frrExportPrefixLists": operations.get("frrExportPrefixLists", []),
        "frrPrefixSequence": operations.get("frrPrefixSequence", 300),
        "wireguardInterfaces": operations.get("wireguardInterfaces", []),
        "wireguardAllowedPrefixes": operations.get("wireguardAllowedPrefixes", [api.get("vip", "")]),
        "fallbackRoutes": operations.get("fallbackRoutes", []),
    }
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    return {"algorithm": "sha256", "checksum": hashlib.sha256(canonical.encode()).hexdigest(), "spec": spec}


def reconcile():
    fabric = request("GET", "/apis/networking.re8ch.com/v1alpha1/advancedfabrics/re8ch")
    role_authority = patroni_role_authority()
    spec = fabric["spec"]
    node_objects = request("GET", "/api/v1/nodes").get("items", [])
    ready = {item["metadata"]["name"]: any(c["type"] == "Ready" and c["status"] == "True"
             for c in item.get("status", {}).get("conditions", [])) for item in node_objects}
    policies = request("GET", "/apis/networking.re8ch.com/v1alpha1/trafficpolicies").get("items", [])
    pods = request("GET", "/api/v1/pods").get("items", [])
    advisor_edges = advisor_items("/api/v1/topology/edges")
    advisor_costs = advisor_items("/api/v1/costs/paths")
    node_index = {node["name"]: node for node in spec["nodes"]}
    control_plane_api = spec.get("controlPlaneApi", {"enabled": False})
    eligible_api_nodes = set(control_plane_api.get("eligibleNodes", []))
    guarded_api_nodes = set(control_plane_api.get("guardedNodes", []))
    api_operations = {item["name"]: item for item in control_plane_api.get("nodeOperations", [])}
    unknown_api_nodes = sorted(eligible_api_nodes - set(node_index))
    if control_plane_api.get("enabled") and unknown_api_nodes:
        raise ValueError(f"controlPlaneApi references unknown nodes: {','.join(unknown_api_nodes)}")
    unknown_guarded_nodes = sorted(guarded_api_nodes - eligible_api_nodes)
    if unknown_guarded_nodes:
        raise ValueError(f"guardedNodes must be eligible: {','.join(unknown_guarded_nodes)}")
    api_apply_safe = all(ready.get(name, False) and node_index[name].get("inventoryComplete", False)
                         for name in guarded_api_nodes)
    effective_apply = (bool(spec.get("applyEnabled")) and not bool(spec.get("emergencyDisable"))
                       and api_apply_safe)
    rankings = {name: {profile: rank_paths(profile, node, advisor_edges, advisor_costs, ready)
                for profile in ("fastest", "balanced", "greedy")} for name, node in node_index.items()}
    resolved = {node["name"]: {"fastest": [], "balanced": [], "greedy": []} for node in spec["nodes"]}
    policy_status = {}
    for policy in sorted(policies, key=policy_key):
        meta, pspec = policy["metadata"], policy["spec"]
        namespace, name = meta["namespace"], meta["name"]
        matched = []
        for pod in pods:
            if pod["metadata"].get("namespace") != namespace or pod.get("spec", {}).get("hostNetwork"):
                continue
            if not selector_matches(pspec.get("podSelector", {}), pod["metadata"].get("labels", {})):
                continue
            ip = pod.get("status", {}).get("podIP")
            node_name = pod.get("spec", {}).get("nodeName")
            if ip and node_name in resolved:
                resolved[node_name][pspec["profile"]].append(ip)
                matched.append({"pod": pod["metadata"]["name"], "node": node_name, "ip": ip})
        policy_status[(namespace, name)] = {"observedGeneration": meta.get("generation", 0),
            "resolvedPods": matched, "conditions": [condition("Resolved", True, "SelectorResolved", f"resolved {len(matched)} pods")],
            "profile": pspec["profile"],
            "selectedNextHops": {item["node"]: rankings.get(item["node"], {}).get(pspec["profile"], [])[:7] for item in matched},
            "lastEvaluationTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    desired = {"apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": "advanced-fabric-desired", "namespace": "kube-system",
                     "labels": {"app.kubernetes.io/name": "re8ch-advanced-fabric"}},
        "data": {name + ".json": json.dumps({"node": name, "mode": spec["mode"],
                  "applyEnabled": effective_apply,
                  "controlPlaneApi": dict(control_plane_api, eligible=name in eligible_api_nodes),
                  "transaction": make_api_transaction(name, control_plane_api, api_operations.get(name, {}),
                                                       name in guarded_api_nodes),
                  "podProfiles": profiles, "pathRankings": rankings.get(name, {}),
                  "weightedEcmp": bool(spec.get("weightedEcmp", {}).get("enabled"))}, sort_keys=True)
                 for name, profiles in resolved.items()}}
    try:
        request("PATCH", "/api/v1/namespaces/kube-system/configmaps/advanced-fabric-desired", desired)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        request("POST", "/api/v1/namespaces/kube-system/configmaps", desired)
    for (namespace, name), status in policy_status.items():
        request("PATCH", f"/apis/networking.re8ch.com/v1alpha1/namespaces/{namespace}/trafficpolicies/{name}/status", {"status": status})
    incomplete = [node["name"] for node in spec["nodes"] if not node.get("inventoryComplete")]
    unavailable = [node["name"] for node in spec["nodes"] if node["role"] == "spine" and
                   (not node.get("inventoryComplete") or not ready.get(node["name"], False))]
    api_ready_nodes = sorted(name for name in eligible_api_nodes if ready.get(name, False))
    status = {"observedGeneration": fabric["metadata"].get("generation", 0),
              "mode": spec["mode"], "applyEnabled": effective_apply,
              "inventoryIncomplete": incomplete, "ineligibleSpines": unavailable,
              "controlPlaneApi": {"enabled": bool(control_plane_api.get("enabled")),
                                  "vip": control_plane_api.get("vip", ""),
                                  "eligibleNodes": sorted(eligible_api_nodes),
                                  "kubernetesReadyNodes": api_ready_nodes},
              "conditions": [condition("InventoryReady", not incomplete, "InventoryEvaluated", ",".join(incomplete) or "complete"),
                             condition("ApplySafe", not unavailable and not spec.get("emergencyDisable"), "SpinesEvaluated", ",".join(unavailable) or "all eligible"),
                             condition("DatabaseRoleAuthorityReady", True, "PatroniObserved", "leader=" + role_authority["leader"]["name"])],
              "lastEvaluationTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    request("PATCH", "/apis/networking.re8ch.com/v1alpha1/advancedfabrics/re8ch/status", {"status": status})


while True:
    try:
        reconcile()
    except Exception as exc:
        print(json.dumps({"event": "advanced-fabric-reconcile-error", "error": str(exc)}), flush=True)
    time.sleep(30)
