#!/usr/bin/env python3
"""Stage isolated RouterOS WireGuard links for the three WSL control planes.

The RouterOS REST credential is read in memory from the existing Kubernetes
Secret. Neither it nor any WireGuard private key is stored in Git or printed.
This stage adds only tunnel addresses and WAN UDP listeners; no cluster route
changes until a separate BGP/data-plane gate has passed.
"""

import argparse
import base64
import ipaddress
import json
import pathlib
import subprocess
import urllib.error
import urllib.request

PRESERVE = "RE8CH preserve MikroTik WAN UDP before restricted-node-6dc3f140f9 default DNAT"
DEFAULT_DNAT = "RE8CH default WAN UDP same-port DNAT to restricted-node-6dc3f140f9"
INPUT_DROP = "defconf: drop all not coming from LAN"


def inventory(path):
    desired = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if desired["authority"] != "RouterOS" or desired["managementIp"] != "192.0.2.240":
        raise ValueError("unexpected RouterOS authority")
    if desired["publicEndpoint"] != "192.0.2.138":
        raise ValueError("public endpoint differs from the validated authority inventory")
    if desired["routerAsn"] != 4200064001:
        raise ValueError("unexpected RouterOS BGP AS")
    links = desired["links"]
    if len(links) != 3 or {link["node"] for link in links} != {"restricted-node-1eb0570f74", "restricted-node-296f36f2fb", "restricted-node-abb3f27a69"}:
        raise ValueError("expected exactly the three reviewed WSL nodes")
    if len({link["listenPort"] for link in links}) != 3:
        raise ValueError("WireGuard listener ports must be unique")
    if any(not 51893 <= link["listenPort"] <= 51895 for link in links):
        raise ValueError("listener port outside the reviewed reservation")
    networks = []
    for link in links:
        router = ipaddress.ip_interface(link["routerAddress"])
        peer = ipaddress.ip_network(link["peerAddress"], strict=True)
        if router.network.prefixlen != 30 or not peer.subnet_of(router.network) or router.ip == peer.network_address:
            raise ValueError("each link requires a distinct router/peer /30")
        if len(link["peerPublicKey"]) != 44:
            raise ValueError("unexpected WireGuard public-key length")
        if ipaddress.ip_network(link["identity"]).prefixlen != 32:
            raise ValueError("control-plane identities must be /32")
        probe = ipaddress.ip_network(link["probeIdentity"], strict=True)
        if probe.prefixlen != 32 or not probe.subnet_of(ipaddress.ip_network("192.0.2.95/24")):
            raise ValueError("BGP probe identity outside its isolated /24")
        if any(router.network.overlaps(prior) for prior in networks):
            raise ValueError("control-plane tunnel subnets overlap")
        networks.append(router.network)
    return desired


def credential():
    result = subprocess.run(
        ["sudo", "k3s", "kubectl", "-n", "ops", "get", "secret", "mikrotik-routeros-admin", "-o", "json"],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(result.stdout)["data"]
    user = base64.b64decode(data["username"])
    password = base64.b64decode(data["password"])
    return base64.b64encode(user + b":" + password).decode()


class RouterOS:
    def __init__(self, address):
        self.base = f"http://{address}/rest"
        self.auth = credential()

    def request(self, path, method="GET", body=None):
        request = urllib.request.Request(
            self.base + path,
            data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers={"Authorization": "Basic " + self.auth, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"RouterOS {method} {path}: HTTP {error.code}") from error
        return json.loads(raw) if raw else {}

    def rows(self, path):
        return self.request(path)

    def create(self, path, body):
        return self.request(path, "PUT", body)

    def update(self, path, item_id, body):
        return self.request(f"{path}/{item_id}", "PATCH", body)

    def delete(self, path, item_id):
        self.request(f"{path}/{item_id}", "DELETE")

    def move_before(self, path, item_id, destination_id):
        self.request(f"{path}/move", "POST", {"numbers": item_id, "destination": destination_id})


def comments(rows):
    return {row.get("comment"): row for row in rows if row.get("comment")}


def require_wan_reservation(router, desired):
    rows = router.rows("/ip/firewall/nat")
    names = {row.get("comment"): (index, row) for index, row in enumerate(rows)}
    preserve = names.get(PRESERVE)
    default = names.get(DEFAULT_DNAT)
    if not preserve or not default or preserve[0] >= default[0]:
        raise RuntimeError("WAN UDP preservation/default DNAT order is unknown")
    for value in (preserve[1], default[1]):
        if value.get("disabled") == "true" or value.get("chain") != "dstnat":
            raise RuntimeError("reviewed WAN NAT rule is disabled or changed")
        if value.get("dst-address") != desired["publicEndpoint"] or value.get("protocol") != "udp":
            raise RuntimeError("reviewed WAN NAT authority changed")
    if preserve[1].get("action") != "accept" or default[1].get("action") != "dst-nat" or default[1].get("to-addresses") != "192.0.2.169":
        raise RuntimeError("reviewed WAN NAT actions changed")
    return preserve[1]


def ensure(router, path, comment, body):
    existing = comments(router.rows(path)).get(comment)
    if existing:
        for field, expected in body.items():
            actual = str(existing.get(field, "")).lower()
            wanted = str(expected).lower()
            if field in ("responder", "disabled"):
                actual = {"yes": "true", "no": "false"}.get(actual, actual)
                wanted = {"yes": "true", "no": "false"}.get(wanted, wanted)
            if field in ("src-address", "dst-address", "local.address", "remote.address"):
                actual = str(ipaddress.ip_network(actual, strict=False))
                wanted = str(ipaddress.ip_network(wanted, strict=False))
            if actual != wanted:
                raise RuntimeError(f"existing managed object differs: {path} {comment} {field}")
        return existing
    return router.create(path, {**body, "comment": comment})


def preflight(router, desired):
    require_wan_reservation(router, desired)
    all_interfaces = router.rows("/interface/wireguard")
    all_addresses = router.rows("/ip/address")
    all_filters = router.rows("/ip/firewall/filter")
    if INPUT_DROP not in comments(all_filters):
        raise RuntimeError("WAN input firewall drop anchor is missing")
    for link in desired["links"]:
        if any(row.get("name") == link["interface"] and row.get("comment") != f'{desired["commentPrefix"]} {link["node"]} interface' for row in all_interfaces):
            raise RuntimeError("WireGuard interface name already in use")
        if any(row.get("listen-port") == str(link["listenPort"]) and row.get("name") != link["interface"] for row in all_interfaces):
            raise RuntimeError("WireGuard listen port already in use")
        if any(row.get("address") == link["routerAddress"] and row.get("interface") != link["interface"] for row in all_addresses):
            raise RuntimeError("RouterOS tunnel address already in use")


def stage(router, desired):
    preflight(router, desired)
    preserve = require_wan_reservation(router, desired)
    ports = {int(value) for value in preserve.get("dst-port", "").split(",") if value}
    ports.update(link["listenPort"] for link in desired["links"])
    router.update("/ip/firewall/nat", preserve[".id"], {"dst-port": ",".join(map(str, sorted(ports)))})
    keys = {}
    for link in desired["links"]:
        prefix = f'{desired["commentPrefix"]} {link["node"]}'
        interface = ensure(router, "/interface/wireguard", prefix + " interface", {
            "name": link["interface"], "listen-port": str(link["listenPort"]),
            "mtu": str(desired["mtu"]), "disabled": "false",
        })
        ensure(router, "/ip/address", prefix + " address", {
            "address": link["routerAddress"], "interface": link["interface"], "disabled": "false",
        })
        peer_body = {
            "interface": link["interface"], "public-key": link["peerPublicKey"],
            "allowed-address": link["peerAddress"], "responder": "yes", "disabled": "false",
        }
        current_peer = comments(router.rows("/interface/wireguard/peers")).get(prefix + " peer")
        if current_peer and current_peer.get("allowed-address") in (
            link["peerAddress"] + "," + link["probeIdentity"],
            link["peerAddress"] + "," + link["probeIdentity"] + "," + link["identity"],
        ):
            peer_body["allowed-address"] = current_peer["allowed-address"]
        ensure(router, "/interface/wireguard/peers", prefix + " peer", peer_body)
        rule = ensure(router, "/ip/firewall/filter", prefix + " WAN UDP", {
            "chain": "input", "action": "accept", "protocol": "udp",
            "dst-address": desired["publicEndpoint"], "dst-port": str(link["listenPort"]),
            "in-interface-list": "WAN", "disabled": "false",
        })
        anchor = comments(router.rows("/ip/firewall/filter"))[INPUT_DROP]
        router.move_before("/ip/firewall/filter", rule[".id"], anchor[".id"])
        keys[link["node"]] = comments(router.rows("/interface/wireguard"))[prefix + " interface"]["public-key"]
    return keys


def inspect(router, desired):
    names = comments(router.rows("/interface/wireguard"))
    peers = comments(router.rows("/interface/wireguard/peers"))
    return {
        link["node"]: {
            "interface": link["interface"],
            "routerPublicKey": names.get(f'{desired["commentPrefix"]} {link["node"]} interface', {}).get("public-key"),
            "peerLastHandshake": peers.get(f'{desired["commentPrefix"]} {link["node"]} peer', {}).get("last-handshake"),
        }
        for link in desired["links"]
    }


def stage_probe_bgp(router, desired):
    """Exchange only 192.0.2.95/24 test /32s; never touch K3s routes."""
    preflight(router, desired)
    if not any(row.get("name") == "re8ch-pod" and row.get("as") == str(desired["routerAsn"])
               for row in router.rows("/routing/bgp/instance")):
        raise RuntimeError("reviewed RouterOS BGP instance is missing")
    all_filters = comments(router.rows("/ip/firewall/filter"))
    input_drop = all_filters[INPUT_DROP]
    fasttrack = all_filters.get("defconf: fasttrack")
    if not fasttrack:
        raise RuntimeError("forward firewall anchor is missing")
    for link in desired["links"]:
        prefix = f'{desired["commentPrefix"]} {link["node"]} probe'
        peer = comments(router.rows("/interface/wireguard/peers")).get(
            f'{desired["commentPrefix"]} {link["node"]} peer')
        if not peer or peer.get("allowed-address") not in (
            link["peerAddress"], link["peerAddress"] + "," + link["probeIdentity"],
            link["peerAddress"] + "," + link["probeIdentity"] + "," + link["identity"]):
            raise RuntimeError("reviewed WireGuard peer is missing or changed")
        allowed = link["peerAddress"] + "," + link["probeIdentity"]
        if peer["allowed-address"].endswith("," + link["identity"]):
            allowed += "," + link["identity"]
        router.update("/interface/wireguard/peers", peer[".id"], {"allowed-address": allowed})
        router_ip = str(ipaddress.ip_interface(link["routerAddress"]).ip)
        peer_ip = str(ipaddress.ip_network(link["peerAddress"]).network_address)
        input_rule = ensure(router, "/ip/firewall/filter", prefix + " BGP input", {
            "chain": "input", "action": "accept", "protocol": "tcp", "dst-port": "179",
            "in-interface": link["interface"], "src-address": peer_ip,
            "dst-address": router_ip, "disabled": "false",
        })
        router.move_before("/ip/firewall/filter", input_rule[".id"], input_drop[".id"])
        forward_rule = ensure(router, "/ip/firewall/filter", prefix + " forward", {
            "chain": "forward", "action": "accept", "in-interface": link["interface"],
            "src-address": link["probeIdentity"], "dst-address": "192.0.2.95/24",
            "disabled": "false",
        })
        router.move_before("/ip/firewall/filter", forward_rule[".id"], fasttrack[".id"])
        chain = "re8ch-cp-probe-" + link["node"].split("-")[0]
        ensure(router, "/routing/filter/rule", prefix + " accept in", {
            "chain": chain + "-in", "rule": f'if (dst=={link["probeIdentity"]}) {{ accept }}',
            "disabled": "false",
        })
        ensure(router, "/routing/filter/rule", prefix + " reject in", {
            "chain": chain + "-in", "rule": "reject", "disabled": "false",
        })
        ensure(router, "/routing/filter/rule", prefix + " accept out", {
            "chain": chain + "-out",
            "rule": "if (dst in 192.0.2.95/24 && dst-len==32) { accept }",
            "disabled": "false",
        })
        ensure(router, "/routing/filter/rule", prefix + " reject out", {
            "chain": chain + "-out", "rule": "reject", "disabled": "false",
        })
        ensure(router, "/routing/bgp/connection", prefix + " BGP", {
            "name": chain, "instance": "re8ch-pod", "afi": "ip",
            "local.address": router_ip, "local.role": "ebgp",
            "remote.address": link["peerAddress"], "remote.as": str(link["asn"]),
            "input.filter": chain + "-in", "output.filter-chain": chain + "-out",
            "disabled": "false",
        })
    return {"probePrefix": "192.0.2.95/24", "peers": [link["node"] for link in desired["links"]]}


def rollback_probe_bgp(router, desired):
    prefix = desired["commentPrefix"]
    if any(str(row.get("comment", "")).startswith(prefix) and " shadow " in row["comment"]
           for row in router.rows("/routing/filter/rule")):
        raise RuntimeError("withdraw shadow identities before rolling back probe BGP")
    removed = []
    for path in ("/routing/bgp/connection", "/routing/filter/rule", "/ip/firewall/filter"):
        for row in router.rows(path):
            if str(row.get("comment", "")).startswith(prefix) and " probe" in row["comment"]:
                router.delete(path, row[".id"])
                removed.append(row["comment"])
    for link in desired["links"]:
        peer = comments(router.rows("/interface/wireguard/peers")).get(
            f'{prefix} {link["node"]} peer')
        if peer and peer.get("allowed-address") == link["peerAddress"] + "," + link["probeIdentity"]:
            router.update("/interface/wireguard/peers", peer[".id"],
                          {"allowed-address": link["peerAddress"]})
    return removed


def rollback_stage(router, desired):
    prefix = desired["commentPrefix"]
    if any(str(row.get("comment", "")).startswith(prefix) for row in router.rows("/routing/bgp/connection")):
        raise RuntimeError("BGP is attached to this mesh; withdraw it before rolling back WireGuard")
    removed = []
    for path in ("/ip/firewall/filter", "/interface/wireguard/peers", "/ip/address", "/interface/wireguard"):
        for row in router.rows(path):
            if str(row.get("comment", "")).startswith(prefix):
                router.delete(path, row[".id"])
                removed.append(row["comment"])
    preserve = require_wan_reservation(router, desired)
    ports = {int(value) for value in preserve.get("dst-port", "").split(",") if value}
    ports.difference_update(link["listenPort"] for link in desired["links"])
    if not ports:
        raise RuntimeError("refusing to empty the WAN UDP preservation list")
    router.update("/ip/firewall/nat", preserve[".id"], {"dst-port": ",".join(map(str, sorted(ports)))})
    return removed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--action", choices=("validate", "preflight", "stage", "inspect", "stage-probe-bgp", "rollback-probe-bgp", "rollback-stage"), required=True)
    args = parser.parse_args()
    desired = inventory(args.inventory)
    if args.action == "validate":
        print("control-plane mesh inventory valid")
        return
    router = RouterOS(desired["managementIp"])
    if args.action == "preflight":
        preflight(router, desired)
        print("RouterOS control-plane mesh preflight passed")
    elif args.action == "stage":
        print(json.dumps(stage(router, desired), sort_keys=True))
    elif args.action == "stage-probe-bgp":
        print(json.dumps(stage_probe_bgp(router, desired), sort_keys=True))
    elif args.action == "rollback-probe-bgp":
        print(json.dumps(rollback_probe_bgp(router, desired)))
    elif args.action == "rollback-stage":
        print(json.dumps(rollback_stage(router, desired)))
    else:
        print(json.dumps(inspect(router, desired), sort_keys=True))


if __name__ == "__main__":
    main()
