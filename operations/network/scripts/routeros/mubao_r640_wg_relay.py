#!/usr/bin/env python3
"""Reserve a separate RouterOS WAN port for MUBAO's R640 WireGuard peer."""

import argparse
import json
import pathlib

from control_plane_mesh import DEFAULT_DNAT, PRESERVE, RouterOS


def contains_port(expression, port):
    for item in expression.split(","):
        item = item.strip()
        if not item:
            continue
        bounds = item.split("-", 1)
        if all(bound.isdigit() for bound in bounds):
            low, high = (int(bounds[0]), int(bounds[-1]))
            if low <= port <= high:
                return True
    return False


def desired_rule(mesh_path, public_path):
    mesh = json.loads(pathlib.Path(mesh_path).read_text())
    public = json.loads(pathlib.Path(public_path).read_text())
    r640 = next(node for node in public["nodes"] if node["name"] == "r640")
    authority = r640["publicAuthority"]
    relay = mesh["r640WgRelay"]
    ports = relay["externalUdpPorts"]
    if (mesh["authority"] != "RouterOS" or authority["type"] != "RouterOS"
            or mesh["managementIp"] != authority["managementIp"] != "192.168.88.1"
            or mesh["publicEndpoint"] != r640["publicIp"] != "223.159.210.222"
            or authority["fallbackLanBackend"] != "192.168.88.251"
            or ports != list(range(51896, 51904))
            or relay["assignments"] != {"mubaoWslData": 51896, "mubaoHostSsh": 51897}
            or relay["backendUdpPort"] != 51893
            or set(ports).intersection(link["listenPort"] for link in mesh["links"])):
        raise RuntimeError("R640 public authority or reserved relay port changed")
    body = {
        "chain": "dstnat", "action": "dst-nat", "protocol": "udp",
        "in-interface-list": "WAN", "dst-address": r640["publicIp"],
        "dst-port": f"{ports[0]}-{ports[-1]}",
        "to-addresses": authority["fallbackLanBackend"],
        "to-ports": str(relay["backendUdpPort"]),
        "comment": relay["comment"],
    }
    return mesh["managementIp"], body


def check_rules(rows, body):
    names = {row.get("comment"): (i, row) for i, row in enumerate(rows)}
    preserve = names.get(PRESERVE)
    default = names.get(DEFAULT_DNAT)
    if not preserve or not default or preserve[0] >= default[0]:
        raise RuntimeError("RouterOS WAN preservation/default DNAT order changed")
    for _, row in (preserve, default):
        if (row.get("disabled") == "true" or row.get("chain") != "dstnat"
                or row.get("protocol") != "udp"
                or row.get("dst-address") != body["dst-address"]
                or row.get("in-interface-list") != "WAN"):
            raise RuntimeError("RouterOS WAN authority changed")
    if (preserve[1].get("action") != "accept"
            or default[1].get("action") != "dst-nat"
            or default[1].get("to-addresses") != body["to-addresses"]
            or any(contains_port(preserve[1].get("dst-port", ""), port)
                   for port in range(51896, 51904))):
        raise RuntimeError("RouterOS UDP port reservation conflicts with relay")
    managed = names.get(body["comment"])
    if managed:
        if any(str(managed[1].get(key, "")) != value for key, value in body.items()):
            raise RuntimeError("existing relay NAT rule differs from inventory")
        if managed[0] >= default[0] or managed[1].get("disabled") == "true":
            raise RuntimeError("relay NAT rule is disabled or after default DNAT")
    for i, row in enumerate(rows):
        if i >= default[0] or row.get("comment") == body["comment"]:
            continue
        if (row.get("chain") == "dstnat" and row.get("protocol") == "udp"
                and row.get("dst-address") == body["dst-address"]
                and any(contains_port(row.get("dst-port", ""), port)
                        for port in range(51896, 51904))):
            raise RuntimeError("another WAN NAT rule claims the R640 relay pool")
    return default[1][".id"], managed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--public-inventory", required=True)
    parser.add_argument("--kube-api-server", choices=("https://10.250.0.147:6443", "https://10.250.0.125:6443", "https://10.250.0.29:6443"))
    parser.add_argument("--action", choices=("preflight", "apply", "rollback"), required=True)
    args = parser.parse_args()
    address, body = desired_rule(args.inventory, args.public_inventory)
    router = RouterOS(address, args.kube_api_server)
    rows = router.rows("/ip/firewall/nat")
    default_id, managed = check_rules(rows, body)
    if args.action == "apply" and not managed:
        created = router.create("/ip/firewall/nat", body)
        router.move_before("/ip/firewall/nat", created[".id"], default_id)
        check_rules(router.rows("/ip/firewall/nat"), body)
    elif args.action == "rollback" and managed:
        router.delete("/ip/firewall/nat", managed[1][".id"])
    print(json.dumps({"action": args.action, "externalUdpPorts": list(range(51896, 51904)),
                      "assignments": {"mubaoWslData": 51896, "mubaoHostSsh": 51897},
                      "backendUdpPort": 51893, "managed": args.action == "apply" or
                      (bool(managed) and args.action == "preflight")}))


if __name__ == "__main__":
    main()
