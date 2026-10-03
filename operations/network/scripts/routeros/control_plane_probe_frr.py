#!/usr/bin/env python3
"""Exchange isolated probe /32 routes over RouterOS WireGuard, not K3s IPs."""

import argparse
import ipaddress
import json
import os
import pathlib
import subprocess


def run(*argv):
    result = subprocess.run(argv, check=True, capture_output=True, text=True)
    if "% " in result.stdout or "% " in result.stderr:
        raise RuntimeError("FRR rejected a probe configuration command")
    return result.stdout


def vtysh(commands):
    args = ["vtysh"]
    for command in commands:
        args += ["-c", command]
    return run(*args)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", required=True)
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--node")
    selector.add_argument("--interface")
    parser.add_argument("--action", choices=("apply", "rollback"), required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("run as root")
    desired = json.loads(pathlib.Path(args.inventory).read_text(encoding="utf-8"))
    links = {link["node"]: link for link in desired["links"]}
    node = args.node or next((name for name, value in links.items()
                              if value["interface"] == args.interface), None)
    if node not in links:
        raise RuntimeError("unknown control-plane link")
    link = links[node]
    router_ip = str(ipaddress.ip_interface(link["routerAddress"]).ip)
    local_ip = str(ipaddress.ip_network(link["peerAddress"]).network_address)
    probe = link["probeIdentity"]
    others = [value["probeIdentity"] for name, value in links.items() if name != node]
    own_asn = link["asn"]
    iface = link["interface"]
    if args.action == "apply":
        route = run("ip", "route", "get", router_ip)
        if f"dev {iface}" not in route:
            raise RuntimeError("RouterOS tunnel path is not active")
        addresses = run("ip", "-4", "addr", "show", "lo")
        if probe not in addresses:
            run("ip", "addr", "add", probe, "dev", "lo")
        commands = ["configure terminal",
                    f"ip prefix-list ROS-CP-PROBE-SELF seq 10 permit {probe}"]
        for seq, peer in enumerate(others, 10):
            commands.append(f"ip prefix-list ROS-CP-PROBE-PEERS seq {seq} permit {peer}")
        commands += [
            "route-map ROS-CP-PROBE-IN permit 10",
            "match ip address prefix-list ROS-CP-PROBE-PEERS",
            "exit",
            "route-map ROS-CP-PROBE-IN deny 100",
            "exit",
            "route-map ROS-CP-PROBE-OUT permit 10",
            "match ip address prefix-list ROS-CP-PROBE-SELF",
            "exit",
            "route-map ROS-CP-PROBE-OUT deny 100",
            "exit",
            f"router bgp {own_asn}",
            f"neighbor {router_ip} remote-as {desired['routerAsn']}",
            f"neighbor {router_ip} update-source {local_ip}",
            f"neighbor {router_ip} timers 10 30",
            "address-family ipv4 unicast",
            f"network {probe}",
            f"neighbor {router_ip} activate",
            # The shadow route-map can also accept the two production peer
            # identities. Keep the bound after a cold boot replays this unit.
            f"neighbor {router_ip} maximum-prefix 4 90 restart 5",
            f"neighbor {router_ip} route-map ROS-CP-PROBE-IN in",
            f"neighbor {router_ip} route-map ROS-CP-PROBE-OUT out",
            "exit-address-family", "end", "write memory",
        ]
    else:
        commands = [
            "configure terminal", f"router bgp {own_asn}",
            "address-family ipv4 unicast", f"no network {probe}",
            "exit-address-family", f"no neighbor {router_ip}", "exit",
            "no route-map ROS-CP-PROBE-IN permit 10",
            "no route-map ROS-CP-PROBE-IN deny 100",
            "no route-map ROS-CP-PROBE-OUT permit 10",
            "no route-map ROS-CP-PROBE-OUT deny 100",
            "no ip prefix-list ROS-CP-PROBE-SELF", "no ip prefix-list ROS-CP-PROBE-PEERS",
            "end", "write memory",
        ]
    vtysh(commands)
    if args.action == "rollback" and probe in run("ip", "-4", "addr", "show", "lo"):
        run("ip", "addr", "del", probe, "dev", "lo")
    print(json.dumps({"node": node, "action": args.action, "neighbor": router_ip,
                      "probeIdentity": probe}))


if __name__ == "__main__":
    main()
