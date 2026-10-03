#!/usr/bin/env python3
"""Compile the one reviewed fabric model into existing GitOps consumers.

The runtime projections document compatibility gaps. They are deliberately
explicit, so a new operations/runtime disagreement fails validation instead
of silently changing WireGuard, BGP, RouterOS, or AdvFab behavior.
"""

import argparse
import copy
import ipaddress
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODEL = Path(__file__).with_name("model.json")
OUTPUTS = {
    "publicMesh": (
        "operations/network/manifests/network/public-mesh/inventory.json",
        "clusters/re8ch/components/network/public-mesh/inventory.json",
    ),
    "privateTransit": (
        "operations/network/manifests/network/private-transit/inventory.json",
        "clusters/re8ch/components/network/private-transit/inventory.json",
    ),
    "podcidrFabric": (
        "operations/network/manifests/network/podcidr-fabric/inventory.json",
        "clusters/re8ch/components/network/podcidr-fabric/inventory.json",
    ),
    "routerosControlMesh": (
        "operations/network/manifests/network/private-transit/routeros-control-mesh.json",
    ),
    "advfabPolicy": (
        "operations/network/manifests/network/private-transit/policy-agent.json",
        "clusters/re8ch/components/network/private-transit/policy-agent.json",
    ),
}
LISTS_BY_NAME = ("nodes", "gateways", "publicNodes", "targets")


def load_model(path=MODEL):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def project(section, overlay):
    value = copy.deepcopy(section)
    for key in overlay.get("removeRoot", []):
        if key not in value:
            raise ValueError(f"runtime projection removes missing root field {key}")
        del value[key]
    for list_name, names in overlay.get("excludeNodes", {}).items():
        if list_name not in LISTS_BY_NAME or list_name not in value:
            raise ValueError(f"unknown projected list {list_name}")
        present = {entry["name"] for entry in value[list_name]}
        if not set(names) <= present:
            raise ValueError(f"projection excludes unknown {list_name} node")
        value[list_name] = [item for item in value[list_name] if item["name"] not in names]
    for name, patch in overlay.get("nodePatches", {}).items():
        matches = [item for list_name in LISTS_BY_NAME for item in value.get(list_name, [])
                   if item["name"] == name]
        if len(matches) != 1:
            raise ValueError(f"projection node {name} matched {len(matches)} records")
        item = matches[0]
        for key in patch.get("remove", []):
            if key not in item:
                raise ValueError(f"projection removes missing {name}.{key}")
            del item[key]
        for key, replacement in patch.get("replace", {}).items():
            if key not in item:
                raise ValueError(f"projection replaces missing {name}.{key}")
            item[key] = replacement
    return value


def outputs(model):
    result = {}
    for section_name, paths in OUTPUTS.items():
        section = model["sections"][section_name]
        result[paths[0]] = section
        if len(paths) == 2:
            result[paths[1]] = project(
                section, model.get("runtimeProjections", {}).get(section_name, {})
            )
    return result


def by_name(items):
    names = [item["name"] for item in items]
    if len(names) != len(set(names)):
        raise ValueError("duplicate fabric node name")
    return {item["name"]: item for item in items}


def validate(model):
    if model["schemaVersion"] != 1 or model["authority"] != "github/main":
        raise ValueError("unsupported fabric model authority/version")
    public = by_name(model["sections"]["publicMesh"]["nodes"])
    pod = by_name(model["sections"]["podcidrFabric"]["nodes"])
    pod.update(by_name(model["sections"]["podcidrFabric"].get("relayNodes", [])))
    pod.update(by_name(model["sections"]["podcidrFabric"].get("routeServers", [])))
    private = model["sections"]["privateTransit"]
    private_names = by_name(private["gateways"] + private["publicNodes"])
    private_names.update(by_name(private.get("controlPlaneRelays", [])))
    router = {item["node"]: item for item in model["sections"]["routerosControlMesh"]["links"]}
    policy = by_name(model["sections"]["advfabPolicy"]["targets"])

    for name in ("restricted-node-6dc3f140f9", "overseas-flk", "overseas-la-2", "restricted-node-abb3f27a69"):
        if name not in public or name not in pod or name not in private_names:
            raise ValueError(f"missing cross-fabric node {name}")
        identity = public[name].get("acceleratedIp", f'10.250.0.{public[name]["id"]}')
        private_identity = private_names[name].get("acceleratedIp")
        if private_identity is None:
            private_identity = f'10.250.0.{private_names[name]["id"]}'
        if identity != pod[name]["acceleratedIp"] or identity != private_identity:
            raise ValueError(f"conflicting accelerated identity for {name}")
        if "podCIDR" in private_names[name] and private_names[name]["podCIDR"] != pod[name]["podCIDR"]:
            raise ValueError(f"conflicting PodCIDR for {name}")
    for name in ("restricted-node-1eb0570f74", "restricted-node-296f36f2fb", "restricted-node-abb3f27a69"):
        if router[name]["identity"] != pod[name]["acceleratedIp"] + "/32":
            raise ValueError(f"RouterOS identity mismatch for {name}")
        if router[name]["podCIDR"] != pod[name]["podCIDR"]:
            raise ValueError(f"RouterOS PodCIDR mismatch for {name}")
    if public["restricted-node-6dc3f140f9"]["publicAuthority"]["type"] != "RouterOS":
        raise ValueError("restricted-node-6dc3f140f9 public authority changed")
    if public["restricted-node-6dc3f140f9"]["publicAuthority"]["gatewayVIP"] != "192.0.2.209":
        raise ValueError("restricted-node-6dc3f140f9 public Gateway authority changed")

    spines = model["routeIntent"]["publicSpines"]
    if len(spines) != 2 or len(set(spines)) != 2:
        raise ValueError("two distinct public spines are required")
    for name in spines:
        if not public[name]["publicIp"] or name not in public["restricted-node-abb3f27a69"]["meshPeers"]:
            raise ValueError(f"MUBAO cannot dial public spine {name}")
        if "restricted-node-abb3f27a69" not in public[name]["meshPeers"]:
            raise ValueError(f"non-reciprocal MUBAO public mesh adjacency {name}")
        if policy[name]["address"] != pod[name]["acceleratedIp"]:
            raise ValueError(f"AdvFab target mismatch for {name}")
        if not policy[name].get("directRouteOnGateway"):
            raise ValueError(f"gateway must keep direct route to {name}")
    if public["restricted-node-abb3f27a69"]["publicIp"] is not None:
        raise ValueError("MUBAO is dial-only")
    if model["routeIntent"]["ecmpMode"] != "per-flow":
        raise ValueError("public mesh must keep per-flow ECMP")
    if model["routeIntent"]["mubaoPodFallback"] != "RouterOS":
        raise ValueError("unverified MUBAO PodCIDR alternative")
    pairs = {tuple(pair) for pair in model["routeIntent"]["controlPodDirectPairs"]}
    if pairs != {("restricted-node-1eb0570f74", "restricted-node-296f36f2fb"), ("restricted-node-296f36f2fb", "restricted-node-1eb0570f74")}:
        raise ValueError("unverified control PodCIDR direct pair")
    for name, item in public.items():
        if "meshPeers" in item:
            for peer in item["meshPeers"]:
                if peer not in public or ("meshPeers" in public[peer] and name not in public[peer]["meshPeers"]):
                    raise ValueError(f"non-reciprocal public mesh edge {name}->{peer}")
    addresses = [ipaddress.ip_address(item["transportIp"]) for item in public.values()]
    if len(addresses) != len(set(addresses)):
        raise ValueError("duplicate public mesh transport address")
    return outputs(model)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("check", "render", "validate"))
    parser.add_argument("--model", type=Path, default=MODEL)
    args = parser.parse_args()
    desired = validate(load_model(args.model))
    if args.action == "validate":
        print(f"fabric model valid: {len(desired)} generated consumers")
        return
    mismatches = []
    for relative, document in desired.items():
        path = ROOT / relative
        if args.action == "render":
            path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        elif json.loads(path.read_text(encoding="utf-8")) != document:
            mismatches.append(relative)
    if mismatches:
        raise SystemExit("fabric outputs drift from model:\n" + "\n".join(mismatches))
    print(f"fabric {args.action}: {len(desired)} consumers synchronized")


if __name__ == "__main__":
    main()
