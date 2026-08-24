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
            if value in seen[field]:
                errors.append(f"duplicate {field}: {value}")
            seen[field].add(value)
        if ipaddress.ip_address(node["transportIp"]) not in network:
            errors.append(f'{node["name"]}: transportIp is outside transportPrefix')
        if node["asn"] != 4200000000 + int(node["id"]):
            errors.append(f'{node["name"]}: ASN does not match 4200000000 + id')
        if node["ztIp"] != f'10.181.22.{node["id"]}':
            errors.append(f'{node["name"]}: ztIp does not match id')
        if node["transportIp"] != f'172.31.92.{node["id"]}':
            errors.append(f'{node["name"]}: transportIp does not match id')
        for route in node.get("rescueRoutes", []):
            if ipaddress.ip_network(route["prefix"]).prefixlen != 32:
                errors.append(f'{node["name"]}: rescue route must be a /32')
            if not ipaddress.ip_address(route["via"]).is_private:
                errors.append(f'{node["name"]}: rescue next hop must be private')
            if not 200 <= int(route["distance"]) <= 254:
                errors.append(f'{node["name"]}: rescue distance must be 200..254')
    if len(inv["nodes"]) != 8:
        errors.append("inventory must contain exactly eight public-authority nodes")
    if errors:
        raise ValueError("\n".join(errors))


def select(inv, name):
    for node in inv["nodes"]:
        if name in (node["name"], node["id"], node["ztIp"]):
            return node
    raise ValueError(f"unknown node: {name}")


def active_nodes(inv, stage):
    return [n for n in inv["nodes"] if int(n["rollout"]) <= stage]


def render_wireguard(inv, node, peers, stage):
    keys = peers.get("publicKeys", peers)
    lines = [
        "[Interface]",
        f'Address = {node["transportIp"]}/32',
        f'ListenPort = {inv["listenPort"]}',
        f'MTU = {inv["mtu"]}',
        "Table = off",
        "PostUp = wg set %i private-key /etc/wireguard/wg-public-mesh.key",
    ]
    for peer in active_nodes(inv, stage):
        if peer["id"] != node["id"]:
            lines.append(f'PostUp = ip route replace {peer["transportIp"]}/32 dev %i scope link')
            lines.append(f'PostDown = ip route del {peer["transportIp"]}/32 dev %i || true')
    for peer in active_nodes(inv, stage):
        if peer["id"] == node["id"]:
            continue
        if peer["id"] not in keys:
            raise ValueError(f'missing public key for peer id {peer["id"]}')
        lines += [
            "",
            "[Peer]",
            f'# {peer["name"]}',
            f'PublicKey = {keys[peer["id"]]}',
            f'AllowedIPs = {peer["transportIp"]}/32, {peer["ztIp"]}/32',
            f'Endpoint = {peer["publicIp"]}:{inv["listenPort"]}',
            "PersistentKeepalive = 15",
        ]
    return "\n".join(lines) + "\n"


def render_frr(inv, node, stage):
    peers = [p for p in active_nodes(inv, stage) if p["id"] != node["id"]]
    lines = [
        "frr defaults traditional",
        f'hostname {node["name"]}',
        "service integrated-vtysh-config",
        "!",
        "bfd",
    ]
    for peer in peers:
        lines += [f' peer {peer["transportIp"]}', "  transmit-interval 1000", "  receive-interval 1000", "  detect-multiplier 3", "  no shutdown", " exit"]
    lines += ["!", f'ip route {node["ztIp"]}/32 Null0 254']
    for route in node.get("rescueRoutes", []):
        lines.append(f'ip route {route["prefix"]} {route["via"]} {route["distance"]}')
    lines += [f'ip prefix-list SELF seq 10 permit {node["ztIp"]}/32']
    for peer in peers:
        lines.append(f'ip prefix-list FROM-{peer["id"]} seq 10 permit {peer["ztIp"]}/32')
    lines += ["!", f'router bgp {node["asn"]}', f' bgp router-id {node["ztIp"]}', " bgp ebgp-requires-policy", " no bgp default ipv4-unicast"]
    for peer in peers:
        lines += [f' neighbor {peer["transportIp"]} remote-as {peer["asn"]}', f' neighbor {peer["transportIp"]} update-source {node["transportIp"]}', f' neighbor {peer["transportIp"]} disable-connected-check', f' neighbor {peer["transportIp"]} timers 1 3', f' neighbor {peer["transportIp"]} bfd']
    lines += [" !", " address-family ipv4 unicast", f'  network {node["ztIp"]}/32']
    for peer in peers:
        lines += [f'  neighbor {peer["transportIp"]} activate', f'  neighbor {peer["transportIp"]} prefix-list FROM-{peer["id"]} in', f'  neighbor {peer["transportIp"]} prefix-list SELF out', f'  neighbor {peer["transportIp"]} maximum-prefix 1 90 restart 5']
    lines += [" exit-address-family", "exit", "!"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", default="clusters/re8ch/components/network/public-mesh/inventory.json")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    wg = sub.add_parser("wireguard")
    wg.add_argument("--node", required=True)
    wg.add_argument("--public-keys", required=True)
    wg.add_argument("--stage", type=int, choices=range(0, 7), required=True)
    frr = sub.add_parser("frr")
    frr.add_argument("--node", required=True)
    frr.add_argument("--stage", type=int, choices=range(0, 7), required=True)
    args = parser.parse_args()
    inv = load_json(args.inventory)
    validate(inv)
    if args.command == "validate":
        print("inventory valid: eight public-authority nodes, unique addresses and private ASNs")
        return
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
