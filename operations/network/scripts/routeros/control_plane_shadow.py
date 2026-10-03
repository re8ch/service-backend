#!/usr/bin/env python3
"""Stage exact K3s node identities as RouterOS mesh shadow routes.

Only the three reviewed /32s may enter or leave the canary BGP sessions.
No public Gateway, API VIP, PodCIDR, default route, or existing BGP session is changed.
"""

import argparse
import ipaddress
import json

from control_plane_mesh import RouterOS, comments, inventory, preflight


def check(router, desired):
    preflight(router, desired)
    filters = comments(router.rows("/routing/filter/rule"))
    peers = comments(router.rows("/interface/wireguard/peers"))
    for link in desired["links"]:
        base = f'{desired["commentPrefix"]} {link["node"]}'
        peer = peers.get(base + " peer")
        expected = link["peerAddress"] + "," + link["probeIdentity"]
        if not peer or peer.get("allowed-address") not in (expected, expected + "," + link["identity"]):
            raise RuntimeError(f"unexpected WireGuard peer for {link['node']}")
        for suffix in ("accept in", "reject in", "accept out", "reject out"):
            if base + " probe " + suffix not in filters:
                raise RuntimeError(f"probe BGP filter missing for {link['node']}: {suffix}")
        if ipaddress.ip_network(link["identity"], strict=True).prefixlen != 32:
            raise RuntimeError("production identity must be an exact /32")


def apply(router, desired):
    check(router, desired)
    filter_path = "/routing/filter/rule"
    for link in desired["links"]:
        base = f'{desired["commentPrefix"]} {link["node"]}'
        peer = comments(router.rows("/interface/wireguard/peers"))[base + " peer"]
        router.update("/interface/wireguard/peers", peer[".id"], {
            "allowed-address": link["peerAddress"] + "," + link["probeIdentity"] + "," + link["identity"]})
        chain = "re8ch-cp-probe-" + link["node"].split("-")[0]
        # Enumerate the other two exact identities; never export a broad /24.
        others = [item["identity"] for item in desired["links"] if item != link]
        entries = [("in", link["identity"])] + [("out", identity) for identity in others]
        for direction, identity in entries:
            expression = f"if (dst=={identity}) {{ accept }}"
            comment = base + " shadow accept " + direction + " " + identity
            rows = comments(router.rows(filter_path))
            existing = rows.get(comment)
            if existing:
                if existing.get("chain") != chain + "-" + direction or existing.get("rule") != expression:
                    raise RuntimeError(f"managed shadow filter drift: {comment}")
                rule = existing
            else:
                rule = router.create(filter_path, {"chain": chain + "-" + direction,
                                                   "rule": expression, "comment": comment,
                                                   "disabled": "false"})
            reject = comments(router.rows(filter_path))[base + " probe reject " + direction]
            router.move_before(filter_path, rule[".id"], reject[".id"])
        for identity in others:
            rule_comment = base + " shadow forward " + identity
            all_rules = comments(router.rows("/ip/firewall/filter"))
            if rule_comment not in all_rules:
                rule = router.create("/ip/firewall/filter", {
                    "chain": "forward", "action": "accept", "in-interface": link["interface"],
                    "src-address": link["identity"], "dst-address": identity,
                    "comment": rule_comment, "disabled": "false"})
            else:
                rule = all_rules[rule_comment]
                for key, expected in (("in-interface", link["interface"]), ("src-address", link["identity"]),
                                      ("dst-address", identity)):
                    if rule.get(key) != expected:
                        raise RuntimeError(f"managed shadow firewall drift: {rule_comment}")
            fasttrack = comments(router.rows("/ip/firewall/filter"))["defconf: fasttrack"]
            router.move_before("/ip/firewall/filter", rule[".id"], fasttrack[".id"])


def rollback(router, desired):
    check(router, desired)
    for link in desired["links"]:
        base = f'{desired["commentPrefix"]} {link["node"]}'
        for path in ("/routing/filter/rule", "/ip/firewall/filter"):
            for row in router.rows(path):
                if row.get("comment", "").startswith(base + " shadow "):
                    router.delete(path, row[".id"])
        peer = comments(router.rows("/interface/wireguard/peers"))[base + " peer"]
        if peer.get("allowed-address") == link["peerAddress"] + "," + link["probeIdentity"] + "," + link["identity"]:
            router.update("/interface/wireguard/peers", peer[".id"], {
                "allowed-address": link["peerAddress"] + "," + link["probeIdentity"]})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--action", choices=("check", "apply", "rollback"), required=True)
    args = parser.parse_args()
    desired = inventory(args.inventory)
    router = RouterOS(desired["managementIp"])
    {"check": check, "apply": apply, "rollback": rollback}[args.action](router, desired)
    print(json.dumps({"action": args.action, "identities": [link["identity"] for link in desired["links"]]}))


if __name__ == "__main__":
    main()
