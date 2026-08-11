#!/usr/bin/env python3
"""Generate a deterministic, credential-free inventory of long-lived K8s services."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "manifests" / "platform-inventory"
LIVE = INVENTORY / "live"
GENERATED = INVENTORY / "generated"


def kubectl(command: str, *args: str) -> dict:
    proc = subprocess.run(
        [*shlex.split(command), *args, "-o", "json"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return json.loads(proc.stdout)


def clean_labels(labels: dict | None) -> dict:
    allowed = ("app", "app.kubernetes.io/name", "app.kubernetes.io/part-of", "re8ch.com/domain")
    labels = labels or {}
    return {key: labels[key] for key in allowed if key in labels}


def ready_condition(status: dict) -> str:
    for condition in status.get("conditions", []):
        if condition.get("type") == "Ready":
            return condition.get("status", "Unknown")
    return "Unknown"


def collect(command: str) -> tuple[dict, dict]:
    nodes = []
    for item in kubectl(command, "get", "nodes").get("items", []):
        addresses = {entry["type"]: entry["address"] for entry in item.get("status", {}).get("addresses", [])}
        info = item.get("status", {}).get("nodeInfo", {})
        nodes.append({
            "name": item["metadata"]["name"],
            "internalIp": addresses.get("InternalIP"),
            "externalIp": addresses.get("ExternalIP"),
            "ready": ready_condition(item.get("status", {})),
            "unschedulable": bool(item.get("spec", {}).get("unschedulable", False)),
            "osImage": info.get("osImage"),
            "kubeletVersion": info.get("kubeletVersion"),
            "containerRuntime": info.get("containerRuntimeVersion"),
        })

    resources = []
    for kind in ("deployments.apps", "statefulsets.apps", "daemonsets.apps", "services", "ingresses.networking.k8s.io"):
        for item in kubectl(command, "get", kind, "-A").get("items", []):
            meta, spec, status = item["metadata"], item.get("spec", {}), item.get("status", {})
            record = {
                "kind": item["kind"],
                "namespace": meta["namespace"],
                "name": meta["name"],
                "labels": clean_labels(meta.get("labels")),
            }
            if item["kind"] in ("Deployment", "StatefulSet"):
                record["desired"] = spec.get("replicas", 1)
                record["ready"] = status.get("readyReplicas", 0)
            elif item["kind"] == "DaemonSet":
                record["desired"] = status.get("desiredNumberScheduled", 0)
                record["ready"] = status.get("numberReady", 0)
            elif item["kind"] == "Service":
                record["type"] = spec.get("type", "ClusterIP")
                record["ports"] = sorted({port.get("port") for port in spec.get("ports", []) if port.get("port")})
            elif item["kind"] == "Ingress":
                record["hosts"] = sorted({rule.get("host") for rule in spec.get("rules", []) if rule.get("host")})
            resources.append(record)

    return (
        {"schemaVersion": "re8ch.com/v1alpha1", "nodes": sorted(nodes, key=lambda x: x["name"])},
        {"schemaVersion": "re8ch.com/v1alpha1", "resources": sorted(resources, key=lambda x: (x["namespace"], x["kind"], x["name"]))},
    )


def encoded(data: dict) -> str:
    # JSON is a strict subset of YAML 1.2 and avoids an unreviewed runtime dependency.
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def render_markdown(nodes: dict, services: dict) -> str:
    lines = ["# RE8CH Live Platform Inventory", "", "Generated; do not edit.", "", "## Nodes", "", "| Node | Ready | Internal IP | External IP | OS |", "|---|---:|---|---|---|"]
    for node in nodes["nodes"]:
        lines.append(f"| {node['name']} | {node['ready']} | {node.get('internalIp') or ''} | {node.get('externalIp') or ''} | {node.get('osImage') or ''} |")
    lines += ["", "## Long-running services", "", "| Namespace | Kind | Name | State / exposure |", "|---|---|---|---|"]
    for item in services["resources"]:
        if item["kind"] in ("Deployment", "StatefulSet", "DaemonSet"):
            detail = f"{item.get('ready', 0)}/{item.get('desired', 0)} ready"
        elif item["kind"] == "Ingress":
            detail = ", ".join(item.get("hosts", []))
        else:
            detail = f"{item.get('type', '')} ports={','.join(map(str, item.get('ports', [])))}"
        lines.append(f"| {item['namespace']} | {item['kind']} | {item['name']} | {detail} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kubectl", default="kubectl")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    nodes, services = collect(args.kubectl)
    outputs = {
        LIVE / "nodes.yaml": encoded(nodes),
        LIVE / "services.yaml": encoded(services),
        GENERATED / "PLATFORM-INVENTORY.md": render_markdown(nodes, services),
    }
    stale = [str(path.relative_to(ROOT)) for path, content in outputs.items() if not path.exists() or path.read_text() != content]
    if args.check:
        if stale:
            print("stale inventory: " + ", ".join(stale), file=sys.stderr)
            return 1
        return 0
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    print("updated " + ", ".join(str(path.relative_to(ROOT)) for path in outputs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
