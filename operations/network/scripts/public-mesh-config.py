#!/usr/bin/env python3
"""Validate and render secret-free RE8CH WireGuard/FRR public-mesh configs."""

import argparse
import ipaddress
import json
import sys


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def validate(inv):
    network = ipaddress.ip_network(inv["transportPrefix"])
    seen = {"id": set(), "name": set(), "ztIp": set(), "transportIp": set(), "publicIp": set(), "asn": set()}
    errors = []
    if inv["interface"] != "wg-public-mesh":
        errors.append("interface must be wg-public-mesh")
    if not 1024 <= int(inv["listenPort"]) <= 65535:
        errors.append("listenPort must be an unprivileged UDP port")
    for node in inv["nodes"]:
        for field in seen:
            value = str(node[field])
            if field == "publicIp" and node[field] is None:
                continue
            if value in seen[field]:
                errors.append(f"duplicate {field}: {value}")
            seen[field].add(value)
        if ipaddress.ip_address(node["transportIp"]) not in network:
            errors.append(f'{node["name"]}: transportIp is outside transportPrefix')
        expected_asn = 4200010000 + int(node["id"]) if node.get("publicMode") == "dial-only" else 4200000000 + int(node["id"])
        if node["asn"] != expected_asn:
            errors.append(f'{node["name"]}: ASN does not match 4200000000 + id')
        if node.get("publicMode") == "dial-only" and node["publicIp"] is not None:
            errors.append(f'{node["name"]}: dial-only peer must not have publicIp')
        if node["ztIp"] != f'10.181.22.{node["id"]}':
            errors.append(f'{node["name"]}: ztIp does not match id')
        if node["transportIp"] != f'172.31.92.{node["id"]}':
            errors.append(f'{node["name"]}: transportIp does not match id')
        if "meshPeers" in node:
            if node.get("acceleratedIp") != f'10.250.0.{node["id"]}':
                errors.append(f'{node["name"]}: mesh expansion requires acceleratedIp')
            if not node["meshPeers"]:
                errors.append(f'{node["name"]}: meshPeers must not be empty')
        for route in node.get("rescueRoutes", []):
            if ipaddress.ip_network(route["prefix"]).prefixlen != 32:
                errors.append(f'{node["name"]}: rescue route must be a /32')
            if not ipaddress.ip_address(route["via"]).is_private:
                errors.append(f'{node["name"]}: rescue next hop must be private')
            if not 200 <= int(route["distance"]) <= 254:
                errors.append(f'{node["name"]}: rescue distance must be 200..254')
        compatibility = node.get("peerCompatibility", {})
        disable_from = compatibility.get("disableFqdnCapabilityFrom", [])
        known_names = {item["name"] for item in inv["nodes"]}
        for source in disable_from:
            if source == node["name"] or source not in known_names:
                errors.append(
                    f'{node["name"]}: invalid disableFqdnCapabilityFrom peer: {source}'
                )
        pod_cidr = node.get("podCIDR")
        if node.get("podCIDRPeers") and not pod_cidr:
            errors.append(f'{node["name"]}: podCIDRPeers requires podCIDR')
        if pod_cidr and not ipaddress.ip_network(pod_cidr).subnet_of(
            ipaddress.ip_network("192.0.2.224/16")
        ):
            errors.append(f'{node["name"]}: podCIDR is outside 192.0.2.224/16')
        for peer_name in node.get("podCIDRPeers", []):
            peer = next((item for item in inv["nodes"] if item["name"] == peer_name), None)
            if peer_name == node["name"] or peer is None or not peer.get("podCIDR"):
                errors.append(f'{node["name"]}: invalid podCIDRPeers peer: {peer_name}')
            elif node["name"] not in peer.get("podCIDRPeers", []):
                errors.append(f'{node["name"]}: non-reciprocal podCIDR peer: {peer_name}')
        api_relay = node.get("apiVipVia")
        if api_relay and (api_relay != {"peer": "restricted-node-6dc3f140f9", "prefix": "192.0.2.220/32"}
                          or node["name"] != "overseas-la"
                          or api_relay["peer"] not in node.get("meshPeers", [])):
            errors.append(f'{node["name"]}: invalid API VIP relay')
    names = {node["name"]: node for node in inv["nodes"]}
    for node in inv["nodes"]:
        for peer_name in node.get("meshPeers", []):
            peer = names.get(peer_name)
            if peer is None or peer is node:
                errors.append(f'{node["name"]}: invalid mesh peer {peer_name}')
            elif "meshPeers" in peer and node["name"] not in peer["meshPeers"]:
                errors.append(f'{node["name"]}: non-reciprocal mesh peer {peer_name}')
    if len(inv["nodes"]) < 2:
        errors.append("inventory requires at least two nodes")
    if errors:
        raise ValueError("\n".join(errors))


def select(inv, name):
    for node in inv["nodes"]:
        if name in (node["name"], node["id"], node["ztIp"]):
            return node
    raise ValueError(f"unknown node: {name}")


def active_nodes(inv, stage):
    return [n for n in inv["nodes"] if int(n["rollout"]) <= stage]


def active_peers(inv, node, stage):
    peers = []
    for peer in active_nodes(inv, stage):
        if peer["id"] == node["id"]:
            continue
        if "meshPeers" in node and peer["name"] not in node["meshPeers"]:
            continue
        if "meshPeers" in peer and node["name"] not in peer["meshPeers"]:
            continue
        peers.append(peer)
    return peers


def render_wireguard(inv, node, peers, stage):
    keys = peers.get("publicKeys", peers)
    lines = [
        "[Interface]",
        f'Address = {node["transportIp"]}/32',
        f'ListenPort = {inv["listenPort"]}',
        f'MTU = {node.get("mtu", inv["mtu"])}',
        "Table = off",
        "PostUp = wg set %i private-key /etc/wireguard/wg-public-mesh.key",
    ]
    for peer in active_peers(inv, node, stage):
        lines.append(f'PostUp = ip route replace {peer["transportIp"]}/32 dev %i scope link')
        lines.append(f'PostDown = ip route del {peer["transportIp"]}/32 dev %i || true')
    for peer in active_peers(inv, node, stage):
        if peer["id"] not in keys:
            raise ValueError(f'missing public key for peer id {peer["id"]}')
        identity = f'10.250.0.{peer["id"]}' if "meshPeers" in node or "meshPeers" in peer else peer["ztIp"]
        allowed = [f'{peer["transportIp"]}/32', f"{identity}/32"]
        if peer["name"] in node.get("podCIDRPeers", []):
            allowed.append(peer["podCIDR"])
        if node.get("apiVipVia", {}).get("peer") == peer["name"]:
            allowed.append(node["apiVipVia"]["prefix"])
        lines += [
            "",
            "[Peer]",
            f'# {peer["name"]}',
            f'PublicKey = {keys[peer["id"]]}',
            f'AllowedIPs = {", ".join(allowed)}',
            *([f'Endpoint = {peer["publicIp"]}:{inv["listenPort"]}'] if peer["publicIp"] else []),
            "PersistentKeepalive = 15",
        ]
    return "\n".join(lines) + "\n"


def render_frr(inv, node, stage):
    peers = active_peers(inv, node, stage)
    expanded = "meshPeers" in node
    has_expanded_peer = any("meshPeers" in peer for peer in peers)
    self_prefix = node["acceleratedIp"] if expanded else node["ztIp"]
    lines = [
        "frr defaults traditional",
        f'hostname {node["name"]}',
        "service integrated-vtysh-config",
        "!",
        "bfd",
    ]
    for peer in peers:
        api_relay_pair = (node.get("apiVipVia", {}).get("peer") == peer["name"]
                          or peer.get("apiVipVia", {}).get("peer") == node["name"])
        if not api_relay_pair:
            lines += [f' peer {peer["transportIp"]}', "  transmit-interval 1000", "  receive-interval 1000", "  detect-multiplier 3", "  no shutdown", " exit"]
    lines += ["!", f'ip route {self_prefix}/32 Null0 254']
    for route in node.get("rescueRoutes", []):
        lines.append(f'ip route {route["prefix"]} {route["via"]} {route["distance"]}')
    lines += [f'ip prefix-list SELF seq 10 permit {self_prefix}/32']
    if node.get("podCIDR"):
        lines.append(f'ip prefix-list SELF seq 20 permit {node["podCIDR"]}')
        lines.append(f'ip prefix-list SELF-IDENTITY seq 10 permit {self_prefix}/32')
    if has_expanded_peer and not expanded:
        lines.append(f'ip prefix-list SELF-ACCEL seq 10 permit 10.250.0.{node["id"]}/32')
    for peer in peers:
        pod_peer = peer["name"] in node.get("podCIDRPeers", [])
        relay_peer = peer.get("apiVipVia", {}).get("peer") == node["name"]
        if pod_peer or relay_peer:
            identity = f'10.250.0.{node["id"]}' if expanded or "meshPeers" in peer else node["ztIp"]
            lines.append(f'ip prefix-list TO-{peer["id"]} seq 10 permit {identity}/32')
            if pod_peer:
                lines.append(f'ip prefix-list TO-{peer["id"]} seq 20 permit {node["podCIDR"]}')
            if relay_peer:
                lines.append(f'ip prefix-list TO-{peer["id"]} seq 30 permit {peer["apiVipVia"]["prefix"]}')
    for peer in peers:
        identity = f'10.250.0.{peer["id"]}' if expanded or "meshPeers" in peer else peer["ztIp"]
        lines.append(f'ip prefix-list FROM-{peer["id"]} seq 10 permit {identity}/32')
        if node.get("apiVipVia", {}).get("peer") == peer["name"]:
            lines.append(f'ip prefix-list FROM-{peer["id"]} seq 15 permit {node["apiVipVia"]["prefix"]}')
        if peer["name"] in node.get("podCIDRPeers", []):
            lines.append(f'ip prefix-list FROM-{peer["id"]} seq 20 permit {peer["podCIDR"]}')
    lines += ["!", f'router bgp {node["asn"]}', f' bgp router-id {node["ztIp"]}', " bgp ebgp-requires-policy", " no bgp default ipv4-unicast"]
    for peer in peers:
        api_relay_pair = (node.get("apiVipVia", {}).get("peer") == peer["name"]
                          or peer.get("apiVipVia", {}).get("peer") == node["name"])
        lines += [f' neighbor {peer["transportIp"]} remote-as {peer["asn"]}', f' neighbor {peer["transportIp"]} update-source {node["transportIp"]}', f' neighbor {peer["transportIp"]} disable-connected-check', f' neighbor {peer["transportIp"]} timers {"3 9" if api_relay_pair else "1 3"}']
        if not api_relay_pair:
            lines.append(f' neighbor {peer["transportIp"]} bfd')
        if node["name"] in peer.get("peerCompatibility", {}).get(
            "disableFqdnCapabilityFrom", []
        ):
            lines.append(f' no neighbor {peer["transportIp"]} capability fqdn')
    lines += [" !", " address-family ipv4 unicast", f'  network {self_prefix}/32']
    if has_expanded_peer and not expanded:
        lines.append(f'  network 10.250.0.{node["id"]}/32')
    for peer in peers:
        if peer.get("apiVipVia", {}).get("peer") == node["name"]:
            lines.append(f'  network {peer["apiVipVia"]["prefix"]}')
    if node.get("podCIDR"):
        lines.append(f'  network {node["podCIDR"]}')
    for peer in peers:
        maximum = 2 if peer["name"] in node.get("podCIDRPeers", []) else 1
        if node.get("apiVipVia", {}).get("peer") == peer["name"]:
            maximum += 1
        outbound = "SELF-ACCEL" if "meshPeers" in peer and not expanded else "SELF-IDENTITY" if node.get("podCIDR") else "SELF"
        if peer["name"] in node.get("podCIDRPeers", []) or peer.get("apiVipVia", {}).get("peer") == node["name"]:
            outbound = f'TO-{peer["id"]}'
        lines += [f'  neighbor {peer["transportIp"]} activate', f'  neighbor {peer["transportIp"]} prefix-list FROM-{peer["id"]} in', f'  neighbor {peer["transportIp"]} prefix-list {outbound} out', f'  neighbor {peer["transportIp"]} maximum-prefix {maximum} 90 restart 5']
    lines += [" exit-address-family", "exit", "!"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", default="manifests/k3s/network/public-mesh/inventory.json")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    wg = sub.add_parser("wireguard")
    wg.add_argument("--node", required=True)
    wg.add_argument("--public-keys", required=True)
    wg.add_argument("--stage", type=int, required=True)
    frr = sub.add_parser("frr")
    frr.add_argument("--node", required=True)
    frr.add_argument("--stage", type=int, required=True)
    args = parser.parse_args()
    inv = load_json(args.inventory)
    validate(inv)
    if args.command == "validate":
        print(f'inventory valid: {len(inv["nodes"])} public-authority nodes, unique addresses and private ASNs')
        return
    if args.stage < 0 or args.stage > max(int(node["rollout"]) for node in inv["nodes"]):
        raise ValueError("stage is outside inventory rollout range")
    node = select(inv, args.node)
    if node["rollout"] > args.stage:
        raise ValueError(f'{node["name"]} is not enabled at stage {args.stage}')
    if args.command == "wireguard":
        print(render_wireguard(inv, node, load_json(args.public_keys), args.stage), end="")
    else:
        print(render_frr(inv, node, args.stage), end="")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
