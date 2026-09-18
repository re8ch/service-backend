#!/usr/bin/env python3
"""Resolve Pods on protected nodes to their top-level workload owners.

This inventory is deliberately read-only. It separates workloads that already
have reviewed placement intent from genuinely unmanaged Pods so operators can
fix the owning controller instead of evicting individual Pods.
"""

import argparse
import json
import subprocess
import sys


SYSTEM_NAMESPACES = {
    "among-clusters",
    "cert-manager",
    "flux-system",
    "kube-system",
    "kueue-system",
    "kyverno",
    "observability-system",
    "opentelemetry-operator",
    "rook-ceph",
    "scheduler-system",
    "scheduling-system",
}
ALLOW_LABEL = "scheduling.re8ch.com/control-plane-allowed"
MANAGED_LABEL = "scheduling.re8ch.com/placement-managed"


def controller_owner(resource):
    owners = resource.get("metadata", {}).get("ownerReferences", [])
    return next((owner for owner in owners if owner.get("controller", True)), None)


def resource_key(resource):
    metadata = resource.get("metadata", {})
    return metadata.get("namespace", "default"), resource.get("kind"), metadata.get("name")


def top_owner(pod, resources):
    index = {resource_key(resource): resource for resource in resources}
    current = pod
    chain = []
    for _ in range(8):
        owner = controller_owner(current)
        if not owner:
            break
        chain.append({"kind": owner["kind"], "name": owner["name"]})
        key = (pod["metadata"]["namespace"], owner["kind"], owner["name"])
        parent = index.get(key)
        if parent is None:
            break
        current = parent
    return current, chain


def pod_template(resource):
    kind = resource.get("kind")
    if kind == "Pod":
        return resource
    if kind == "CronJob":
        return resource.get("spec", {}).get("jobTemplate", {}).get("spec", {}).get("template", {})
    return resource.get("spec", {}).get("template", {})


def has_required_placement(spec):
    if spec.get("nodeSelector"):
        return True
    required = (
        ((spec.get("affinity") or {}).get("nodeAffinity") or {})
        .get("requiredDuringSchedulingIgnoredDuringExecution", {})
        .get("nodeSelectorTerms", [])
    )
    return bool(required)


def classify(pod, owner):
    namespace = pod["metadata"]["namespace"]
    template = pod_template(owner)
    labels = {}
    labels.update(owner.get("metadata", {}).get("labels", {}))
    labels.update(template.get("metadata", {}).get("labels", {}))
    labels.update(pod.get("metadata", {}).get("labels", {}))
    if namespace in SYSTEM_NAMESPACES:
        return "system"
    if owner.get("kind") == "DaemonSet":
        return "daemon"
    if labels.get(ALLOW_LABEL) == "true":
        return "approved"
    spec = template.get("spec", {})
    if labels.get(MANAGED_LABEL) == "true" or has_required_placement(spec):
        return "managed-conflict"
    return "unmanaged"


def inventory(items, nodes):
    pods = [item for item in items if item.get("kind") == "Pod"]
    records = []
    for pod in pods:
        spec = pod.get("spec", {})
        if spec.get("nodeName") not in nodes or pod.get("status", {}).get("phase") != "Running":
            continue
        owner, chain = top_owner(pod, items)
        classification = classify(pod, owner)
        template_spec = pod_template(owner).get("spec", {})
        records.append(
            {
                "node": spec["nodeName"],
                "namespace": pod["metadata"]["namespace"],
                "pod": pod["metadata"]["name"],
                "ownerKind": owner.get("kind", "Pod"),
                "ownerName": owner.get("metadata", {}).get("name", pod["metadata"]["name"]),
                "ownerChain": chain,
                "classification": classification,
                "nodeSelector": template_spec.get("nodeSelector", {}),
                "hasRequiredNodeAffinity": has_required_placement(template_spec),
            }
        )
    return sorted(records, key=lambda item: (item["node"], item["namespace"], item["ownerName"], item["pod"]))


def load_cluster_items(context):
    command = [
        "kubectl",
        *( ["--context", context] if context else [] ),
        "get",
        "pods,replicasets,deployments,statefulsets,daemonsets,jobs,cronjobs",
        "--all-namespaces",
        "-o",
        "json",
    ]
    return json.loads(subprocess.check_output(command))["items"]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("nodes", nargs="+")
    parser.add_argument("--context")
    parser.add_argument("--format", choices=("json", "tsv"), default="tsv")
    args = parser.parse_args(argv)
    records = inventory(load_cluster_items(args.context), set(args.nodes))
    if args.format == "json":
        json.dump(records, sys.stdout, indent=2)
        print()
        return
    for record in records:
        print(
            record["node"],
            f'{record["namespace"]}/{record["pod"]}',
            f'{record["ownerKind"]}/{record["ownerName"]}',
            record["classification"],
            sep="\t",
        )


if __name__ == "__main__":
    main()
