#!/usr/bin/env python3
"""Project the credential-free cluster snapshot into a small Agent catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).parents[2]
DEFAULT_INPUT = ROOT / "inventory/platform/live/services.yaml"
DEFAULT_OUTPUT = ROOT / "inventory/platform/catalog/runtime-services.json"
WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet"}


def health(resource: dict) -> str:
    desired = int(resource.get("desired", 0))
    ready = int(resource.get("ready", 0))
    if desired == 0:
        return "scaled-to-zero"
    if ready >= desired:
        return "ready"
    return "degraded"


def project(snapshot: dict) -> dict:
    resources = snapshot["resources"]
    services = {}
    ingresses = {}
    for item in resources:
        key = (item["namespace"], item["name"])
        if item["kind"] == "Service":
            services[key] = {
                "type": item.get("type", "ClusterIP"),
                "ports": item.get("ports", []),
            }
        elif item["kind"] == "Ingress":
            ingresses[key] = item.get("hosts", [])

    workloads = []
    for item in resources:
        if item["kind"] not in WORKLOAD_KINDS:
            continue
        namespace = item["namespace"]
        name = item["name"]
        entry = {
            "ref": f"kubernetes/{namespace}/{item['kind'].lower()}/{name}",
            "namespace": namespace,
            "name": name,
            "kind": item["kind"],
            "desired": int(item.get("desired", 0)),
            "ready": int(item.get("ready", 0)),
            "health": health(item),
        }
        key = (namespace, name)
        if key in services:
            entry["service"] = services[key]
        if key in ingresses:
            entry["hosts"] = ingresses[key]
        workloads.append(entry)

    workloads.sort(key=lambda item: (item["namespace"], item["kind"], item["name"]))
    return {
        "apiVersion": "catalog.re8ch.com/v1alpha1",
        "kind": "RuntimeServiceCatalog",
        "metadata": {
            "name": "re8ch-cluster-runtime",
            "source": "inventory/platform/live/services.yaml",
            "authority": "kubernetes",
        },
        "summary": {
            "workloads": len(workloads),
            "ready": sum(item["health"] == "ready" for item in workloads),
            "degraded": sum(item["health"] == "degraded" for item in workloads),
            "scaledToZero": sum(item["health"] == "scaled-to-zero" for item in workloads),
        },
        "items": workloads,
    }


def encoded(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = encoded(project(json.loads(args.input.read_text())))
    if args.check:
        if not args.output.exists() or args.output.read_text() != result:
            raise SystemExit(f"stale generated catalog: {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
