#!/usr/bin/env python3
"""Validate and render the two-fabric private transit WireGuard/FRR topology."""

import argparse
import ipaddress
import json
import sys


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def nodes(inv):
    return inv["gateways"] + inv["workers"] + inv["publicNodes"]


def by_name(inv, name):
    for node in nodes(inv):
        if name in (node["name"], str(node["id"]), node["ztIp"]):
            return node
    raise ValueError(f"unknown node: {name}")


def gateway(inv, fabric):
    name = inv["fabrics"][fabric]["gateway"]
    return by_name(inv, name)


def transport(inv, fabric, node):
    net = ipaddress.ip_network(inv["fabrics"][fabric]["prefix"])
    return str(net.network_address + int(node["id"]))


def accelerated(inv, node):
    net = ipaddress.ip_network(inv["acceleratedPrefix"])
    return str(net.network_address + int(node["id"]))


def validate(inv):
    all_nodes = nodes(inv)
    seen = set()
    accel_net = ipaddress.ip_network(inv["acceleratedPrefix"])
    if accel_net.prefixlen != 24 or not accel_net.is_private:
        raise ValueError("acceleratedPrefix must be a private /24")
    for node in all_nodes:
        if node["name"] in seen:
            raise ValueError(f'duplicate node: {node["name"]}')
        seen.add(node["name"])
        if node["ztIp"] != f'10.181.22.{node["id"]}':
            raise ValueError(f'{node["name"]}: id and ztIp differ')
    for fabric, spec in inv["fabrics"].items():
        net = ipaddress.ip_network(spec["prefix"])
        if net.prefixlen != 24 or not net.is_private:
            raise ValueError(f"{fabric}: transport prefix must be private /24")
        if not 1024 <= int(spec["port"]) <= 65535:
            raise ValueError(f"{fabric}: invalid UDP port")
        if gateway(inv, fabric).get("fabric") != fabric:
            raise ValueError(f"{fabric}: gateway mismatch")


def fabrics_for(inv, node):
    if node in inv["gateways"]:
        return [node["fabric"]]
    if node in inv["publicNodes"]:
        return list(inv["fabrics"])
    return []


def peers_for(inv, node, fabric):
    gw = gateway(inv, fabric)
    if node["name"] == gw["name"]:
        return inv["publicNodes"]
    return [gw]


def key_for(keys, node, fabric):
    key = f'{node["name"]}:{fabric}'
    try:
        return keys.get("publicKeys", keys)[key]
    except KeyError as exc:
        raise ValueError(f"missing public key: {key}") from exc


def allowed_for_spoke(inv, node, fabric, gw):
    values = [f'{transport(inv, fabric, gw)}/32']
    if node in inv["workers"]:
        values += [f'{n["ztIp"]}/32' for n in inv["publicNodes"]]
        values.append(f'{gw["ztIp"]}/32')
    else:
        # Public spokes must retain a control-plane path when the shared
        # ZeroTier identity network or R640 is unavailable.
        values.append(f'{gw["ztIp"]}/32')
        if gw.get("podCIDR"):
            values.append(gw["podCIDR"])
        values += [f'{accelerated(inv, n)}/32' for n in nodes(inv)]
    return values


def render_wg(inv, node, fabric, keys):
    spec = inv["fabrics"][fabric]
    gw = gateway(inv, fabric)
    iface = spec["interface"]
    lines = [
        "[Interface]",
        f'Address = {transport(inv, fabric, node)}/32',
        f'ListenPort = {spec["port"]}',
        f'MTU = {inv["mtu"]}',
        "Table = off",
        f'PostUp = wg set %i private-key /etc/wireguard/{iface}.key',
        "PostUp = sysctl -q -w net.ipv4.conf.%i.rp_filter=0",
    ]
    for peer in peers_for(inv, node, fabric):
        lines.append(f'PostUp = ip route replace {transport(inv, fabric, peer)}/32 dev %i scope link')
        lines.append(f'PostDown = ip route del {transport(inv, fabric, peer)}/32 dev %i || true')
        if node == gw and peer.get("podCIDR"):
            lines.append(f'PostUp = ip route replace {peer["ztIp"]}/32 dev %i src {node["ztIp"]} metric 5')
            lines.append(f'PostDown = ip route del {peer["ztIp"]}/32 dev %i || true')
        if node in inv["publicNodes"] and peer == gw:
            lines.append(f'PostUp = ip route replace {gw["ztIp"]}/32 dev %i src {node["ztIp"]} metric 5')
            lines.append(f'PostDown = ip route del {gw["ztIp"]}/32 dev %i || true')
    for peer in peers_for(inv, node, fabric):
        if node["name"] == gw["name"]:
            allowed = [f'{transport(inv, fabric, peer)}/32', f'{accelerated(inv, peer)}/32']
            if peer.get("podCIDR"):
                allowed += [f'{peer["ztIp"]}/32', peer["podCIDR"]]
        else:
            allowed = allowed_for_spoke(inv, node, fabric, gw)
        lines += ["", "[Peer]", f'# {peer["name"]}', f'PublicKey = {key_for(keys, peer, fabric)}', f'AllowedIPs = {", ".join(allowed)}']
        if node["name"] == gw["name"] and peer in inv["publicNodes"]:
            lines.append(f'Endpoint = {peer["publicIp"]}:{spec["port"]}')
        lines.append("PersistentKeepalive = 15")
    return "\n".join(lines) + "\n"


def plist(name, prefixes):
    return [f'ip prefix-list {name} seq {10 * (i + 1)} permit {prefix}' for i, prefix in enumerate(prefixes)]


def render_gateway_frr(inv, node):
    fabric = node["fabric"]
    peers = inv["publicNodes"] + inv["workers"]
    private_out = [f'{accelerated(inv, node)}/32'] + [f'{accelerated(inv, n)}/32' for n in inv["workers"]]
    public_out = [f'{accelerated(inv, node)}/32'] + [f'{accelerated(inv, n)}/32' for n in inv["publicNodes"]]
    lines = ["frr defaults traditional", f'hostname {node["name"]}-private-transit', "service integrated-vtysh-config", "!"]
    lines += plist("PT-PRIVATE-OUT", private_out) + plist("PT-PUBLIC-OUT", public_out)
    for peer in peers:
        lines += plist(f'PT-FROM-{peer["id"]}', [f'{accelerated(inv, peer)}/32'])
    lines += ["!", f'ip route {accelerated(inv, node)}/32 Null0 254', f'router bgp {node["asn"]}', f' bgp router-id {node["ztIp"]}', " bgp ebgp-requires-policy", " no bgp default ipv4-unicast"]
    for peer in peers:
        address = peer["ztIp"] if peer in inv["workers"] else transport(inv, fabric, peer)
        source = node["ztIp"] if peer in inv["workers"] else transport(inv, fabric, node)
        lines += [f' neighbor {address} remote-as {peer["asn"]}', f' neighbor {address} update-source {source}', f' neighbor {address} timers 3 9', f' neighbor {address} bfd']
        if peer in inv["publicNodes"]:
            lines.append(f' neighbor {address} disable-connected-check')
    lines += [" !", " address-family ipv4 unicast", f'  network {accelerated(inv, node)}/32']
    for peer in peers:
        out = "PT-PRIVATE-OUT" if peer in inv["publicNodes"] else "PT-PUBLIC-OUT"
        address = peer["ztIp"] if peer in inv["workers"] else transport(inv, fabric, peer)
        lines += [f'  neighbor {address} activate', f'  neighbor {address} prefix-list PT-FROM-{peer["id"]} in', f'  neighbor {address} prefix-list {out} out', f'  neighbor {address} maximum-prefix 1 90 restart 5']
    lines += [" exit-address-family", "exit", "!"]
    return "\n".join(lines) + "\n"


def render_worker_frr(inv, node):
    public = [f'{accelerated(inv, n)}/32' for n in inv["publicNodes"]]
    gateways = [f'{accelerated(inv, n)}/32' for n in inv["gateways"]]
    lines = ["frr defaults traditional", f'hostname {node["name"]}-private-transit', "service integrated-vtysh-config", "!"]
    lines += plist("PT-IN", public + gateways) + plist("PT-SELF", [f'{accelerated(inv, node)}/32'])
    lines += ["!", f'ip route {accelerated(inv, node)}/32 Null0 254', f'router bgp {node["asn"]}', f' bgp router-id {node["ztIp"]}', " bgp ebgp-requires-policy", " bgp bestpath as-path multipath-relax", " no bgp default ipv4-unicast"]
    for fabric in inv["fabrics"]:
        gw = gateway(inv, fabric)
        lines += [f' neighbor {gw["ztIp"]} remote-as {gw["asn"]}', f' neighbor {gw["ztIp"]} update-source {node["ztIp"]}', f' neighbor {gw["ztIp"]} timers 3 9']
        lines.append(f' neighbor {gw["ztIp"]} bfd')
    lines += [" !", " address-family ipv4 unicast", f'  network {accelerated(inv, node)}/32']
    for fabric in inv["fabrics"]:
        gw = gateway(inv, fabric)
        lines += [f'  neighbor {gw["ztIp"]} activate', f'  neighbor {gw["ztIp"]} prefix-list PT-IN in', f'  neighbor {gw["ztIp"]} prefix-list PT-SELF out', f'  neighbor {gw["ztIp"]} maximum-prefix {len(public) + len(gateways)} 90 restart 5']
    lines += ["  maximum-paths 2", " exit-address-family", "exit", "!"]
    return "\n".join(lines) + "\n"


def render_public_fragment(inv, node):
    private = [f'{accelerated(inv, n)}/32' for n in inv["gateways"] + inv["workers"]]
    lines = plist("PT-PRIVATE-IN", private) + plist("PT-SELF-OUT", [f'{accelerated(inv, node)}/32']) + [f'ip route {accelerated(inv, node)}/32 Null0 254', f'router bgp {node["asn"]}', ' bgp bestpath as-path multipath-relax']
    for fabric in inv["fabrics"]:
        gw = gateway(inv, fabric)
        lines += [f' neighbor {transport(inv, fabric, gw)} remote-as {gw["asn"]}', f' neighbor {transport(inv, fabric, gw)} update-source {transport(inv, fabric, node)}', f' neighbor {transport(inv, fabric, gw)} disable-connected-check', f' neighbor {transport(inv, fabric, gw)} timers 3 9', f' neighbor {transport(inv, fabric, gw)} bfd']
    lines += [" address-family ipv4 unicast", f'  network {accelerated(inv, node)}/32']
    for fabric in inv["fabrics"]:
        gw = gateway(inv, fabric)
        lines += [f'  neighbor {transport(inv, fabric, gw)} activate', f'  neighbor {transport(inv, fabric, gw)} prefix-list PT-PRIVATE-IN in', f'  neighbor {transport(inv, fabric, gw)} prefix-list PT-SELF-OUT out', f'  neighbor {transport(inv, fabric, gw)} maximum-prefix {len(private)} 90 restart 5']
    lines += ["  maximum-paths 2", " exit-address-family", "exit", "!"]
    return "\n".join(lines) + "\n"


def render_identity_unit(inv, node):
    address = accelerated(inv, node)
    return "\n".join([
        "[Unit]",
        "Description=RE8CH private-transit accelerated service identity",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=oneshot",
        "RemainAfterExit=yes",
        f"ExecStart=/usr/sbin/ip address replace {address}/32 dev lo",
        f"ExecStop=/usr/sbin/ip address del {address}/32 dev lo",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", default="clusters/re8ch/components/network/private-transit/inventory.json")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    wg = sub.add_parser("wireguard")
    wg.add_argument("--node", required=True)
    wg.add_argument("--fabric", choices=["a1", "b1"], required=True)
    wg.add_argument("--public-keys", required=True)
    frr = sub.add_parser("frr")
    frr.add_argument("--node", required=True)
    identity = sub.add_parser("identity-unit")
    identity.add_argument("--node", required=True)
    args = parser.parse_args()
    inv = load(args.inventory)
    validate(inv)
    if args.command == "validate":
        print(f'private transit inventory valid: dedicated accelerated /24, two fabrics, two gateways, {len(inv["workers"])} workers, {len(inv["publicNodes"])} public nodes')
        return
    node = by_name(inv, args.node)
    if args.command == "identity-unit":
        print(render_identity_unit(inv, node), end="")
    elif args.command == "wireguard":
        if args.fabric not in fabrics_for(inv, node):
            raise ValueError(f'{node["name"]} does not participate in {args.fabric}')
        print(render_wg(inv, node, args.fabric, load(args.public_keys)), end="")
    elif node in inv["gateways"]:
        print(render_gateway_frr(inv, node), end="")
    elif node in inv["workers"]:
        print(render_worker_frr(inv, node), end="")
    else:
        print(render_public_fragment(inv, node), end="")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
