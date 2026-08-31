#!/usr/bin/env python3
"""Reconcile the isolated POCO F4 WireGuard debugging boundary.

Run in the routeros-node-controller Pod. RouterOS and phone public-key
credentials are read from Kubernetes Secrets and are never printed.
"""

import argparse
import base64
import ipaddress
import json
import pathlib
import ssl
import urllib.error
import urllib.request

KUBE_API = "https://kubernetes.default.svc"
KUBE_CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
KUBE_TOKEN = "/var/run/secrets/kubernetes.io/serviceaccount/token"
ROUTER_SECRET = ("ops", "mikrotik-routeros-admin")


def load_desired(path):
    desired = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    subnet = ipaddress.ip_network(desired["subnet"], strict=True)
    router = ipaddress.ip_interface(desired["routerTunnelAddress"])
    phone = ipaddress.ip_network(desired["phoneAddress"], strict=True)
    if subnet.prefixlen != 30 or router.ip not in subnet or not phone.subnet_of(subnet):
        raise ValueError("debug boundary must be one /30 containing router and phone")
    if router.ip == phone.network_address:
        raise ValueError("router and phone addresses must differ")
    for protected in desired["protectedNetworks"]:
        if subnet.overlaps(ipaddress.ip_network(protected, strict=True)):
            raise ValueError(f"debug subnet overlaps protected network {protected}")
    if desired["interface"] in desired["protectedInterfaces"]:
        raise ValueError("debug interface name collides with a protected interface")
    if desired["listenPort"] in (51820, 51891):
        raise ValueError("listen port collides with an existing RE8CH WireGuard service")
    reservation = desired["wanUdpReservation"]
    if not reservation["comment"].startswith("RE8CH preserve MikroTik WAN UDP"):
        raise ValueError("WAN UDP reservation must target the reviewed RouterOS preservation rule")
    if not reservation["defaultDnatComment"].startswith("RE8CH default WAN UDP"):
        raise ValueError("WAN UDP reservation must identify the reviewed default DNAT rule")
    if not desired["commentPrefix"].startswith("RE8CH POCO DEBUG"):
        raise ValueError("rollback scope requires the RE8CH POCO DEBUG prefix")
    return desired


def kubernetes_secret(namespace, name):
    token = pathlib.Path(KUBE_TOKEN).read_text(encoding="utf-8").strip()
    request = urllib.request.Request(
        f"{KUBE_API}/api/v1/namespaces/{namespace}/secrets/{name}",
        headers={"Authorization": "Bearer " + token},
    )
    context = ssl.create_default_context(cafile=KUBE_CA)
    payload = json.load(urllib.request.urlopen(request, context=context, timeout=15))
    return {key: base64.b64decode(value).decode() for key, value in payload["data"].items()}


class RouterOS:
    def __init__(self, address):
        secret = kubernetes_secret(*ROUTER_SECRET)
        raw = f'{secret["username"]}:{secret["password"]}'.encode()
        self.auth = base64.b64encode(raw).decode()
        self.base = f"http://{address}/rest"

    def request(self, path, method="GET", body=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Authorization": "Basic " + self.auth, "Content-Type": "application/json"},
        )
        try:
            response = urllib.request.urlopen(request, timeout=20)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            raise RuntimeError(f"RouterOS {method} {path}: {detail}") from error
        raw = response.read()
        return json.loads(raw) if raw else {}

    def rows(self, path):
        return self.request(path)

    def create(self, path, body):
        return self.request(path, "PUT", body)

    def delete(self, path, item_id):
        self.request(f"{path}/{item_id}", "DELETE")

    def update(self, path, item_id, body):
        return self.request(f"{path}/{item_id}", "PATCH", body)

    def move_before(self, path, item_id, destination_id):
        self.request(f"{path}/move", "POST", {"numbers": item_id, "destination": destination_id})


def comments(desired):
    prefix = desired["commentPrefix"]
    return {
        "interface": f"{prefix} WireGuard interface",
        "address": f"{prefix} router tunnel address",
        "peer": f"{prefix} POCO F4 peer",
        "input": f"{prefix} allow roaming WireGuard",
        "adb": f"{prefix} allow R640 ADB only",
        "return": f"{prefix} allow established ADB return",
        "drop_source": f"{prefix} isolate phone source",
        "drop_destination": f"{prefix} deny other access to phone",
    }


def by_comment(router, path):
    return {row.get("comment"): row for row in router.rows(path) if row.get("comment")}


def assert_no_conflicts(router, desired):
    managed = desired["commentPrefix"]
    needle = (desired["subnet"].split("/")[0].rsplit(".", 1)[0] + ".", str(desired["listenPort"]))
    paths = ("/ip/address", "/ip/route", "/ip/pool", "/ip/firewall/address-list", "/interface/wireguard")
    conflicts = []
    for path in paths:
        for row in router.rows(path):
            if str(row.get("comment", "")).startswith(managed):
                continue
            serialized = json.dumps(row, sort_keys=True)
            if any(value in serialized for value in needle):
                conflicts.append({"path": path, "id": row.get(".id"), "comment": row.get("comment", "")})
    if conflicts:
        raise RuntimeError("desired CIDR or UDP port conflicts with unmanaged RouterOS objects: " + json.dumps(conflicts))

    live_interfaces = {row.get("name") for row in router.rows("/interface")}
    missing = sorted(set(desired["protectedInterfaces"]) - live_interfaces)
    if missing:
        raise RuntimeError("protected RouterOS interfaces are missing before change: " + json.dumps(missing))


def ensure(router, path, comment, body):
    existing = by_comment(router, path).get(comment)
    if existing:
        return existing
    return router.create(path, {**body, "comment": comment})


def udp_ports(value):
    return {int(port) for port in str(value or "").split(",") if port}


def wan_udp_rules(router, desired):
    reservation = desired["wanUdpReservation"]
    rules = router.rows("/ip/firewall/nat")
    by_name = {row.get("comment"): (index, row) for index, row in enumerate(rules)}
    preserve = by_name.get(reservation["comment"])
    default = by_name.get(reservation["defaultDnatComment"])
    if not preserve or not default:
        raise RuntimeError("reviewed WAN UDP preservation/default DNAT rule is missing")
    if preserve[0] >= default[0]:
        raise RuntimeError("WAN UDP preservation rule must precede the default R640 DNAT")
    preserve_row, default_row = preserve[1], default[1]
    expected_preserve = {"chain": "dstnat", "action": "accept", "protocol": "udp",
                         "dst-address": desired["publicAddress"]}
    expected_default = {"chain": "dstnat", "action": "dst-nat", "protocol": "udp",
                        "dst-address": desired["publicAddress"], "to-addresses": desired["r640Address"]}
    if any(preserve_row.get(key) != value for key, value in expected_preserve.items()):
        raise RuntimeError("WAN UDP preservation rule no longer matches the reviewed shape")
    if any(default_row.get(key) != value for key, value in expected_default.items()):
        raise RuntimeError("default R640 UDP DNAT rule no longer matches the reviewed shape")
    if preserve_row.get("disabled") == "true" or default_row.get("disabled") == "true":
        raise RuntimeError("WAN UDP preservation/default DNAT rule is disabled")
    return preserve_row, default_row


def reserve_wan_udp(router, desired):
    preserve, _ = wan_udp_rules(router, desired)
    ports = udp_ports(preserve.get("dst-port"))
    ports.add(int(desired["listenPort"]))
    value = ",".join(str(port) for port in sorted(ports))
    if value != preserve.get("dst-port"):
        preserve = router.update("/ip/firewall/nat", preserve[".id"], {"dst-port": value})
    return preserve


def release_wan_udp(router, desired):
    preserve, _ = wan_udp_rules(router, desired)
    ports = udp_ports(preserve.get("dst-port"))
    ports.discard(int(desired["listenPort"]))
    value = ",".join(str(port) for port in sorted(ports))
    if not value:
        raise RuntimeError("refusing to empty the shared WAN UDP preservation rule")
    if value != preserve.get("dst-port"):
        router.update("/ip/firewall/nat", preserve[".id"], {"dst-port": value})


def reconcile(router, desired):
    assert_no_conflicts(router, desired)
    c = comments(desired)
    phone_secret = desired["phonePublicKeySecret"]
    public_key = kubernetes_secret(phone_secret["namespace"], phone_secret["name"])[phone_secret["key"]].strip()
    if len(public_key) != 44:
        raise RuntimeError("phone WireGuard public key has an unexpected length")
    reserve_wan_udp(router, desired)

    interface = ensure(router, "/interface/wireguard", c["interface"], {
        "name": desired["interface"], "listen-port": str(desired["listenPort"]), "disabled": "false"
    })
    ensure(router, "/ip/address", c["address"], {
        "address": desired["routerTunnelAddress"], "interface": desired["interface"], "disabled": "false"
    })
    ensure(router, "/interface/wireguard/peers", c["peer"], {
        "interface": desired["interface"], "public-key": public_key,
        "allowed-address": desired["phoneAddress"], "responder": "yes", "disabled": "false"
    })

    filters = by_comment(router, "/ip/firewall/filter")
    input_rule = ensure(router, "/ip/firewall/filter", c["input"], {
        "chain": "input", "action": "accept", "protocol": "udp",
        "dst-address": desired["publicAddress"], "dst-port": str(desired["listenPort"]), "disabled": "false"
    })
    input_anchor = filters.get("defconf: drop all not coming from LAN") or by_comment(router, "/ip/firewall/filter").get("defconf: drop all not coming from LAN")
    if not input_anchor:
        raise RuntimeError("input firewall anchor is missing")
    router.move_before("/ip/firewall/filter", input_rule[".id"], input_anchor[".id"])

    rules = (
        (c["adb"], {"chain": "forward", "action": "accept", "protocol": "tcp", "src-address": desired["r640Address"], "dst-address": desired["phoneAddress"].split("/")[0], "dst-port": str(desired["adbPort"]), "out-interface": desired["interface"], "disabled": "false"}),
        (c["return"], {"chain": "forward", "action": "accept", "connection-state": "established,related", "src-address": desired["phoneAddress"].split("/")[0], "dst-address": desired["r640Address"], "in-interface": desired["interface"], "disabled": "false"}),
        (c["drop_source"], {"chain": "forward", "action": "drop", "src-address": desired["phoneAddress"].split("/")[0], "in-interface": desired["interface"], "disabled": "false"}),
        (c["drop_destination"], {"chain": "forward", "action": "drop", "dst-address": desired["phoneAddress"].split("/")[0], "out-interface": desired["interface"], "disabled": "false"}),
    )
    forward_anchor = by_comment(router, "/ip/firewall/filter").get("defconf: fasttrack")
    if not forward_anchor:
        raise RuntimeError("forward firewall anchor is missing")
    for comment, body in reversed(rules):
        rule = ensure(router, "/ip/firewall/filter", comment, body)
        router.move_before("/ip/firewall/filter", rule[".id"], forward_anchor[".id"])
        forward_anchor = rule

    return {"changed": True, "interface": interface.get("name", desired["interface"]), "phone": desired["phoneAddress"]}


def rollback(router, desired):
    prefix = desired["commentPrefix"]
    removed = []
    for path in ("/ip/firewall/filter", "/interface/wireguard/peers", "/ip/address", "/interface/wireguard"):
        for row in list(router.rows(path)):
            if str(row.get("comment", "")).startswith(prefix):
                router.delete(path, row[".id"])
                removed.append({"path": path, "comment": row.get("comment")})
    release_wan_udp(router, desired)
    removed.append({"path": "/ip/firewall/nat", "port": desired["listenPort"]})
    return {"removed": removed}


def status(router, desired):
    prefix = desired["commentPrefix"]
    paths = ("/interface/wireguard", "/interface/wireguard/peers", "/ip/address", "/ip/firewall/filter")
    result = {path: [{k: v for k, v in row.items() if k not in ("private-key", "public-key", "preshared-key")}
                     for row in router.rows(path) if str(row.get("comment", "")).startswith(prefix)] for path in paths}
    preserve, default = wan_udp_rules(router, desired)
    result["wanUdpReservation"] = {
        "portReserved": int(desired["listenPort"]) in udp_ports(preserve.get("dst-port")),
        "preserveComment": preserve.get("comment"),
        "defaultDnatComment": default.get("comment"),
    }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("validate", "check", "apply", "rollback"))
    parser.add_argument("--desired", required=True)
    args = parser.parse_args()
    desired = load_desired(args.desired)
    if args.action == "validate":
        print(json.dumps({"valid": True, "subnet": desired["subnet"], "listenPort": desired["listenPort"]}))
        return
    router = RouterOS(desired["routerAddress"])
    if args.action == "check":
        assert_no_conflicts(router, desired)
        result = status(router, desired)
    elif args.action == "apply":
        result = reconcile(router, desired)
    else:
        result = rollback(router, desired)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
