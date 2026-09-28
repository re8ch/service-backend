#!/usr/bin/env python3
"""Use the declared AdvFab node identity for K3s traffic to R640."""

import argparse
import json
import subprocess


TABLE = "re8ch_k3s_control_snat"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", default="/etc/re8ch/podcidr-fabric-inventory.json")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    with open(args.inventory, encoding="utf-8") as handle:
        inventory = json.load(handle)
    addresses = json.loads(subprocess.check_output(["ip", "-j", "address", "show", "dev", "lo"]))
    local_ips = {
        address["local"]
        for interface in addresses
        for address in interface.get("addr_info", [])
        if address.get("family") == "inet"
    }
    candidates = [
        node for node in inventory["nodes"]
        if node.get("peerMode") == "transport"
        and node.get("kubernetesNodeIp") == node["acceleratedIp"]
        and node["acceleratedIp"] in local_ips
    ]
    if len(candidates) != 1:
        raise SystemExit(f"expected one migrated AdvFab node on lo; found {len(candidates)}")
    node_ip = candidates[0]["acceleratedIp"]
    control_ips = [node["acceleratedIp"] for node in inventory["nodes"] if node["name"] == "r640"]
    if len(control_ips) != 1:
        raise SystemExit("expected exactly one R640 control-plane identity")
    control_ip = control_ips[0]
    rules = f"""table ip {TABLE} {{
  chain postrouting {{
    type nat hook postrouting priority srcnat; policy accept;
    ip daddr {control_ip} tcp dport 6443 snat to {node_ip}
  }}
}}
"""
    subprocess.run(["nft", "-c", "-f", "-"], input=rules, text=True, check=True)
    if args.check:
        print(f"{candidates[0]['name']}: {node_ip} -> {control_ip}:6443")
        return
    subprocess.run(["nft", "delete", "table", "ip", TABLE], check=False, capture_output=True)
    subprocess.run(["nft", "-f", "-"], input=rules, text=True, check=True)


if __name__ == "__main__":
    main()
