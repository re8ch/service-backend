#!/usr/bin/env python3
"""Project the shared node inventory into safe, read-only cluster consumers."""

import argparse
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "inventory/nodes.yaml"
OUTPUT = ROOT / "clusters/re8ch/components/cluster-inventory/configmap.yaml"


class ManifestDumper(yaml.SafeDumper):
    pass


def represent_string(dumper, value):
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


ManifestDumper.add_representer(str, represent_string)


def render():
    source = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    if source.get("kind") != "InfrastructureTargetList":
        raise ValueError("shared node inventory has the wrong kind")
    public_nodes = {}
    fabric_nodes = []
    names = set()
    for item in source["items"]:
        name = item["metadata"]["name"]
        if name in names:
            raise ValueError(f"duplicate node: {name}")
        names.add(name)
        cluster = item.get("spec", {}).get("cluster", {})
        fabric = cluster.get("fabric")
        if not fabric:
            continue
        if cluster.get("membership") != "active":
            raise ValueError(f"fabric node is not an active cluster member: {name}")
        network = cluster.get("network", {})
        capacity = network.get("uplinkMbps")
        region = network.get("region")
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            raise ValueError(f"invalid uplinkMbps for {name}")
        if not isinstance(region, str) or not region or region.endswith("unverified"):
            raise ValueError(f"invalid region for {name}")
        if not fabric.get("inventoryComplete"):
            raise ValueError(f"incomplete fabric inventory for {name}")
        public_nodes[name] = {"capacityMbps": capacity, "region": region}
        fabric_nodes.append({"name": name, "region": region, "uplinkMbps": capacity, **fabric})
    if not {"restricted-node-6dc3f140f9", "overseas-la", "overseas-kl"}.issubset(public_nodes):
        raise ValueError("missing an existing public delivery node")
    if {"qwen-1", "qwen-2", "qwen-3"} & public_nodes.keys():
        raise ValueError("unobserved inference nodes cannot become public candidates")
    values = yaml.safe_dump({"advancedFabric": {"nodes": fabric_nodes}}, sort_keys=False)
    manifest = {
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": "cluster-node-inventory", "namespace": "flux-system",
                     "labels": {"reconcile.fluxcd.io/watch": "Enabled"}},
        "data": {
            "nodes.json": json.dumps(public_nodes, sort_keys=True, separators=(",", ":")),
            "advanced-fabric-values.yaml": values,
        },
    }
    return yaml.dump(manifest, Dumper=ManifestDumper, sort_keys=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != expected:
            raise SystemExit(f"stale cluster node inventory projection: {OUTPUT}")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(expected, encoding="utf-8")


if __name__ == "__main__":
    main()
