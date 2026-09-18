#!/usr/bin/env python3
"""Safely converge VLAgent pods on Ready nodes when offline nodes block a DaemonSet rollout."""

import argparse
import json
import subprocess
import time


NAMESPACE = "observability-system"
DAEMONSET = "vlagent-re8ch"
CONTAINER = "vlagent"


def kubectl_json(*args):
    return json.loads(subprocess.check_output(["kubectl", *args, "-o", "json"]))


def container_args(spec):
    for container in spec.get("containers", []):
        if container.get("name") == CONTAINER:
            return container.get("args", [])
    return []


def is_ready_pod(pod):
    return any(
        condition.get("type") == "Ready" and condition.get("status") == "True"
        for condition in pod.get("status", {}).get("conditions", [])
    )


def ready_node_names(nodes):
    result = set()
    for node in nodes.get("items", []):
        if any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in node.get("status", {}).get("conditions", [])
        ):
            result.add(node["metadata"]["name"])
    return result


def owned_by_daemonset(pod):
    return any(
        owner.get("kind") == "DaemonSet" and owner.get("name") == DAEMONSET
        for owner in pod.get("metadata", {}).get("ownerReferences", [])
    )


def host_path_volumes(spec):
    return [
        volume.get("name", "<unnamed>")
        for volume in spec.get("volumes", [])
        if "hostPath" in volume
    ]


def validate_namespace_admission(namespace, daemonset):
    labels = namespace.get("metadata", {}).get("labels", {})
    enforce = labels.get("pod-security.kubernetes.io/enforce", "privileged")
    volumes = host_path_volumes(daemonset["spec"]["template"]["spec"])
    if volumes and enforce != "privileged":
        names = ", ".join(volumes)
        raise SystemExit(
            f"refusing rollout: namespace enforces Pod Security {enforce}, "
            f"but the desired VLAgent requires hostPath volumes ({names})"
        )


def outdated_ready_node_pods(daemonset, pods, nodes):
    desired = container_args(daemonset["spec"]["template"]["spec"])
    ready_nodes = ready_node_names(nodes)
    return [
        pod
        for pod in pods.get("items", [])
        if owned_by_daemonset(pod)
        and not pod.get("metadata", {}).get("deletionTimestamp")
        and pod.get("spec", {}).get("nodeName") in ready_nodes
        and container_args(pod.get("spec", {})) != desired
    ]


def wait_for_replacement(node_name, desired_args, timeout_seconds):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        pods = kubectl_json("-n", NAMESPACE, "get", "pods")
        for pod in pods.get("items", []):
            if (
                owned_by_daemonset(pod)
                and pod.get("spec", {}).get("nodeName") == node_name
                and not pod.get("metadata", {}).get("deletionTimestamp")
                and container_args(pod.get("spec", {})) == desired_args
                and is_ready_pod(pod)
            ):
                return pod["metadata"]["name"]
        time.sleep(5)
    raise TimeoutError(f"replacement VLAgent on {node_name} did not become Ready")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="gracefully replace selected pods")
    parser.add_argument("--max-pods", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args()
    if args.max_pods < 1:
        raise SystemExit("--max-pods must be positive")

    daemonset = kubectl_json("-n", NAMESPACE, "get", "daemonset", DAEMONSET)
    namespace = kubectl_json("get", "namespace", NAMESPACE)
    validate_namespace_admission(namespace, daemonset)
    pods = kubectl_json("-n", NAMESPACE, "get", "pods")
    nodes = kubectl_json("get", "nodes")
    targets = outdated_ready_node_pods(daemonset, pods, nodes)
    desired_args = container_args(daemonset["spec"]["template"]["spec"])

    for pod in targets:
        print(f"outdated\t{pod['spec']['nodeName']}\t{pod['metadata']['name']}")
    if not args.apply:
        print(f"dry-run: {len(targets)} Ready-node pod(s) require convergence")
        return

    for pod in targets[: args.max_pods]:
        node_name = pod["spec"]["nodeName"]
        current_nodes = kubectl_json("get", "nodes")
        if node_name not in ready_node_names(current_nodes):
            raise SystemExit(f"refusing rollout: {node_name} is no longer Ready")
        pod_name = pod["metadata"]["name"]
        subprocess.run(
            ["kubectl", "-n", NAMESPACE, "delete", "pod", pod_name, "--wait=false"],
            check=True,
        )
        replacement = wait_for_replacement(node_name, desired_args, args.timeout_seconds)
        print(f"ready\t{node_name}\t{replacement}")


if __name__ == "__main__":
    main()
