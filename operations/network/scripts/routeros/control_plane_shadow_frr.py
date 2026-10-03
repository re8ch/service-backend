#!/usr/bin/env python3
"""Keep RouterOS control-plane identities in FRR as low preference fallbacks."""

import argparse
import ipaddress
import json
import os
import pathlib

from control_plane_probe_frr import run, vtysh


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--action", choices=("apply", "rollback"), required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("run as root")
    desired = json.loads(pathlib.Path(args.inventory).read_text(encoding="utf-8"))
    links = {link["node"]: link for link in desired["links"]}
    if set(links) != {"restricted-node-1eb0570f74", "restricted-node-296f36f2fb", "restricted-node-abb3f27a69"} or args.node not in links:
        raise RuntimeError("unexpected node inventory")
    link = links[args.node]
    router_ip = str(ipaddress.ip_interface(link["routerAddress"]).ip)
    own = link["identity"]
    others = [other["identity"] for name, other in links.items() if name != args.node]
    running = vtysh(["show running-config"])
    if f"neighbor {router_ip} route-map ROS-CP-PROBE-IN in" not in running:
        raise RuntimeError("probe BGP neighbor is not configured")
    if f"neighbor {router_ip} route-map ROS-CP-PROBE-OUT out" not in running:
        raise RuntimeError("probe BGP export filter is not configured")
    if args.action == "apply":
        route = run("ip", "route", "get", router_ip)
        if f"dev {link['interface']}" not in route:
            raise RuntimeError("RouterOS tunnel is not active")
        commands = ["configure terminal", f"ip prefix-list ROS-CP-SHADOW-SELF seq 10 permit {own}"]
        for seq, identity in enumerate(others, 10):
            commands.append(f"ip prefix-list ROS-CP-SHADOW-PEERS seq {seq} permit {identity}")
        commands += [
            "route-map ROS-CP-PROBE-IN permit 20",
            "match ip address prefix-list ROS-CP-SHADOW-PEERS",
            "set local-preference 50",
            "exit",
            "route-map ROS-CP-PROBE-OUT permit 20",
            "match ip address prefix-list ROS-CP-SHADOW-SELF",
            "exit",
            f"router bgp {link['asn']}",
            "address-family ipv4 unicast",
            f"neighbor {router_ip} maximum-prefix 4 90 restart 5",
            "exit-address-family", "end", "write memory",
        ]
    else:
        commands = [
            "configure terminal", f"router bgp {link['asn']}",
            "address-family ipv4 unicast",
            f"neighbor {router_ip} maximum-prefix 2 90 restart 5",
            "exit-address-family", "exit",
            "no route-map ROS-CP-PROBE-IN permit 20",
            "no route-map ROS-CP-PROBE-OUT permit 20",
            "no ip prefix-list ROS-CP-SHADOW-SELF",
            "no ip prefix-list ROS-CP-SHADOW-PEERS",
            "end", "write memory",
        ]
    vtysh(commands)
    vtysh([f"clear bgp {router_ip} soft in"])
    vtysh([f"clear bgp {router_ip} soft out"])
    print(json.dumps({"node": args.node, "action": args.action, "localPreference": 50,
                      "identity": own, "acceptedPeers": others}))


if __name__ == "__main__":
    main()
