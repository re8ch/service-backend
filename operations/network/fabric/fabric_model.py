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
import hashlib
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
HOST_CONTRACT = "clusters/re8ch/components/network/podcidr-fabric/host-network-contract.json"


def host_contract(model):
    """Compile an inventory-mode contract; never imply host adoption."""
    contract = copy.deepcopy(model["hostNetwork"])
    if contract["mode"] != "inventory" or contract["adoptedNodes"]:
        raise ValueError("host apply requires a separately implemented adoption controller")
    if contract["ownership"]["frr"]["wholeFileReplacement"]:
        raise ValueError("shared FRR configuration cannot be replaced wholesale")
    if contract["ownership"]["advfab"]["ecmpMode"] != "per-flow":
        raise ValueError("host ECMP must be per-flow")
    if contract["rollout"]["maxUnavailable"] != 1 or not contract["rollout"]["controlPlaneSerial"]:
        raise ValueError("host rollout must be serial")
    if contract["rollback"]["requiresClusterApi"] or contract["rollback"]["executor"] != "host-local":
        raise ValueError("network rollback must work without the cluster API")
    sections = model["sections"]
    fabric = sections["podcidrFabric"]
    nodes = fabric["routeServers"] + fabric["nodes"] + fabric.get("relayNodes", [])
    identities = [node["acceleratedIp"] for node in nodes]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate host accelerated identity")
    public = {f'10.250.0.{node["id"]}': node for node in sections["publicMesh"]["nodes"]}
    transit = sections["privateTransit"]
    transit_nodes = {f'10.250.0.{node["id"]}': node
                     for group in ("gateways", "workers", "publicNodes") for node in transit[group]}
    control = {node["node"]: node for node in sections["routerosControlMesh"]["links"]}
    plans = {}
    for node in sorted(nodes, key=lambda item: item["name"]):
        name, identity = node["name"], node["acceleratedIp"]
        interfaces = []
        if identity in public:
            entry = public[identity]
            interfaces.append({"name": "wg-public-mesh", "modelSection": "publicMesh",
                               "inventoryName": entry["name"], "transportIp": entry["transportIp"],
                               "mtu": entry.get("mtu", sections["publicMesh"]["mtu"]),
                               "privateKeyRef": "host:/etc/wireguard/wg-public-mesh.key",
                               "declaredWriters": ["host-wg-quick", "podcidr-fabric-reconciler"]})
        if identity in transit_nodes:
            entry = transit_nodes[identity]
            for key, spec in transit["fabrics"].items():
                if entry in transit["publicNodes"] or entry.get("fabric") == key:
                    interfaces.append({"name": spec["interface"], "modelSection": "privateTransit",
                                       "inventoryName": entry["name"], "fabric": key,
                                       "declaredWriters": ["host-wg-quick", "podcidr-fabric-reconciler"]})
        if name in control:
            interfaces.append({"name": control[name]["interface"], "modelSection": "routerosControlMesh",
                               "declaredWriters": ["host-wg-quick", "re8ch-control-pod-mesh"]})
        if name in {item["name"] for item in fabric["routeServers"]}:
            interfaces.append({"name": "wg-wsl-lan", "modelSection": "podcidrFabric",
                               "declaredWriters": ["host-wg-quick", "podcidr-fabric-reconciler"]})
        if name in ("restricted-node-abb3f27a69", "restricted-node-6dc3f140f9"):
            interfaces.append({"name": "wg-pt-mubao", "modelSection": "podcidrFabric.relayNodes",
                               "declaredWriters": ["host-wg-quick", "podcidr-fabric-reconciler"]})
        interface_names = [item["name"] for item in interfaces]
        if len(interface_names) != len(set(interface_names)):
            raise ValueError(f"duplicate interface scope on {name}")
        plans[name] = {"node": name, "identity": identity, "podCIDR": node["podCIDR"],
                       "asn": node["asn"], "mode": "inventory", "applyEligible": False,
                       "wireguard": interfaces,
                       "frr": {"path": "/etc/frr/frr.conf", "replacement": "owned-scopes-only",
                               "modelSections": ["podcidrFabric"] + (["publicMesh"] if identity in public else []) + (["privateTransit"] if identity in transit_nodes else []) + (["routerosControlMesh"] if name in control else []),
                               "declaredWriters": ["podcidr-fabric-reconciler", "host-frr-persisted-config"]},
                       "adoptionBlockers": ["live-writer-audit-required", "transactional-host-controller-required"],
                       "controlPodRoutePolicy": model["routeIntent"]["controlPodDirectPairs"] if name in control else []}
    contract["nodes"] = plans
    # Content identity is reproducible; promotion binds it to a signed Git SHA.
    payload = json.dumps({"model": model, "plans": plans}, sort_keys=True, separators=(",", ":"))
    contract["modelDigest"] = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
    return contract


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
        section = copy.deepcopy(model["sections"][section_name])
        if section_name == "routerosControlMesh":
            intent = model["routeIntent"]
            section["podRoutePolicy"] = {
                "directPairs": intent["controlPodDirectPairs"],
                "fallbackTable": intent["controlPodFallbackTable"],
                "ruleBase": intent["controlPodRuleBase"],
            }
        result[paths[0]] = section
        if len(paths) == 2:
            result[paths[1]] = project(
                section, model.get("runtimeProjections", {}).get(section_name, {})
            )
    result[HOST_CONTRACT] = host_contract(model)
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
    if public["restricted-node-6dc3f140f9"]["publicAuthority"]["gatewayVIP"] != "192.0.2.213":
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
    if model["routeIntent"]["controlPodFallbackTable"] != 15425 or model["routeIntent"]["controlPodRuleBase"] != 11450:
        raise ValueError("unreviewed control PodCIDR policy table or priority")
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
    parser.add_argument("action", choices=("check", "render", "validate", "host-plan"))
    parser.add_argument("--model", type=Path, default=MODEL)
    parser.add_argument("--node")
    args = parser.parse_args()
    desired = validate(load_model(args.model))
    if args.action == "host-plan":
        contract = desired[HOST_CONTRACT]
        if args.node not in contract["nodes"]:
            parser.error("--node must name a declared host")
        print(json.dumps({"modelDigest": contract["modelDigest"], **contract["nodes"][args.node]}, indent=2))
        return
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
