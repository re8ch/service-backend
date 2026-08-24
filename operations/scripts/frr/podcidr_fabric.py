#!/usr/bin/env python3
"""Validate and render the retained-node RE8CH PodCIDR BGP fabric."""

import argparse
import ipaddress
import json
from pathlib import Path


DEFAULT_INVENTORY = Path("clusters/re8ch/components/network/podcidr-fabric/inventory.json")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate(inventory):
    native = ipaddress.ip_network(inventory["nativeRoutingCIDR"])
    accelerated = ipaddress.ip_network(inventory["acceleratedCIDR"])
    service = ipaddress.ip_network(inventory["serviceCIDR"])
    if native != ipaddress.ip_network("192.0.2.57/16"):
        raise ValueError("nativeRoutingCIDR must remain 192.0.2.57/16")
    seen_names, seen_pods, seen_asns = set(), set(), set()
    for node in inventory["routeServers"] + inventory["nodes"]:
        name, asn = node["name"], int(node["asn"])
        if name in seen_names or asn in seen_asns:
            raise ValueError(f"duplicate name or ASN: {name}/{asn}")
        seen_names.add(name)
        seen_asns.add(asn)
        ipaddress.ip_address(node["internalIp"])
        if "rrInterconnectIp" in node:
            ipaddress.ip_address(node["rrInterconnectIp"])
    for node in inventory["nodes"]:
        pod = ipaddress.ip_network(node["podCIDR"])
        accel = ipaddress.ip_address(node["acceleratedIp"])
        if pod.prefixlen != 24 or not pod.subnet_of(native) or pod in seen_pods:
            raise ValueError(f"invalid or duplicate PodCIDR: {pod}")
        if accel not in accelerated:
            raise ValueError(f"accelerated IP outside {accelerated}: {accel}")
        if node["peerMode"] not in ("identity", "transport"):
            raise ValueError(f"invalid peerMode for {node['name']}")
        seen_pods.add(pod)
    if native.overlaps(accelerated) or native.overlaps(service) or accelerated.overlaps(service):
        raise ValueError("routing address families must not overlap")


def node_by_name(inventory, name):
    return next(node for node in inventory["nodes"] if node["name"] == name)


def neighbor_address(node, route_server):
    return route_server["transportIp"] if node["peerMode"] == "transport" else route_server["internalIp"]


def render_spoke(inventory, node):
    lines = [
        "configure terminal",
        "no ip prefix-list POD-FABRIC-IN",
        "ip prefix-list POD-FABRIC-IN seq 10 permit 192.0.2.57/16 ge 24 le 24",
        "ip prefix-list POD-FABRIC-IN seq 20 permit 192.0.2.65/24 ge 32 le 32",
        "ip prefix-list POD-FABRIC-IN seq 30 permit 192.0.2.121/24 ge 32 le 32",
        "ip prefix-list POD-FABRIC-IN seq 1000 deny any",
        "no ip prefix-list POD-FABRIC-SELF",
        f"ip prefix-list POD-FABRIC-SELF seq 10 permit {node['podCIDR']}",
        f"ip prefix-list POD-FABRIC-SELF seq 20 permit {node['acceleratedIp']}/32",
        "ip prefix-list POD-FABRIC-SELF seq 1000 deny any",
        "route-map POD-FABRIC-SELF-NH permit 10",
        "match ip address prefix-list POD-FABRIC-SELF",
        f"set ip next-hop {node['internalIp']}",
        "exit",
        f"ip route {node['podCIDR']} Null0 254",
        f"router bgp {node['asn']}",
        "bgp ebgp-requires-policy",
        "bgp bestpath as-path multipath-relax",
        "no bgp default ipv4-unicast",
        "address-family ipv4 unicast",
        f"network {node['podCIDR']}",
        f"network {node['acceleratedIp']}/32",
    ]
    for route_server in inventory["routeServers"]:
        neighbor = neighbor_address(node, route_server)
        lines += [
            f"neighbor {neighbor} activate",
            f"neighbor {neighbor} prefix-list POD-FABRIC-IN in",
            f"neighbor {neighbor} prefix-list POD-FABRIC-SELF out",
            f"neighbor {neighbor} route-map POD-FABRIC-SELF-NH out",
            f"neighbor {neighbor} maximum-prefix 64 90 restart 5",
        ]
    lines += ["maximum-paths 2", "exit-address-family", "end", "write memory"]
    return "\n".join(lines) + "\n"


def expected(inventory):
    retained = len(inventory["nodes"]) + len(inventory["routeServers"])
    remote_routes = retained - 1
    return {
        "retainedNodes": retained,
        "spokes": len(inventory["nodes"]),
        "routeServers": len(inventory["routeServers"]),
        "expectedRemotePodRoutesPerNode": remote_routes,
    }


def control_plane_identity_routes(node_name):
    """Return the protected control-plane identity exceptions.

    restricted-node-6dc3f140f9 is not an ordinary ZeroTier L2 node. A1 and B1 each have a direct
    public-underlay WireGuard path to restricted-node-6dc3f140f9, while restricted-node-6dc3f140f9 must retain its
    RouterOS/WG boundary routes.
    """
    if node_name == "restricted-node-1eb0570f74":
        return ["ip route 192.0.2.178/32 wg-pt-a1 5"]
    return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", default=str(DEFAULT_INVENTORY))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    render = sub.add_parser("render-spoke")
    render.add_argument("--node", required=True)
    sub.add_parser("expected")
    args = parser.parse_args()
    inventory = load(args.inventory)
    validate(inventory)
    if args.command == "validate":
        print(json.dumps(expected(inventory), sort_keys=True))
    elif args.command == "render-spoke":
        print(render_spoke(inventory, node_by_name(inventory, args.node)), end="")
    else:
        print(json.dumps(expected(inventory), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
