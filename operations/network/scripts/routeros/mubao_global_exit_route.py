#!/usr/bin/env python3
"""Exact RouterOS transit for MUBAO to the LA2 SOCKS exit via A1.

This does not alter WAN, DNAT, public Gateway, or the existing R640 path.
Credentials are read in memory from the ops Secret by control_plane_mesh.
"""

import argparse
import json

from control_plane_mesh import RouterOS, inventory


EXIT = "10.250.0.220/32"
SOURCE = "10.250.0.29"
ROUTER_PEER = "172.31.96.2"
PEER_COMMENT = "RE8CH CP MESH a1-wsl-zt peer"
ROUTE_COMMENT = "RE8CH CP MESH mubao global exit LA2 via A1"
OUT_COMMENT = "RE8CH CP MESH mubao to LA2 SOCKS"
BACK_COMMENT = "RE8CH CP MESH LA2 SOCKS return to mubao"
BASE_ALLOWED = "172.31.96.2/32,172.31.97.147/32,10.250.0.147/32"
EXIT_ALLOWED = BASE_ALLOWED + "," + EXIT


def one(rows, comment):
    matches = [row for row in rows if row.get("comment") == comment]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one RouterOS object: {comment}")
    return matches[0]


def state(router):
    peer = one(router.rows("/interface/wireguard/peers"), PEER_COMMENT)
    if peer.get("interface") != "wg-cp-a1":
        raise RuntimeError("A1 peer interface changed")
    if peer.get("allowed-address") not in (BASE_ALLOWED, EXIT_ALLOWED):
        raise RuntimeError("A1 cryptokey prefixes changed")
    routes = [row for row in router.rows("/ip/route") if row.get("comment") == ROUTE_COMMENT]
    out = [row for row in router.rows("/ip/firewall/filter") if row.get("comment") == OUT_COMMENT]
    back = [row for row in router.rows("/ip/firewall/filter") if row.get("comment") == BACK_COMMENT]
    if any(len(items) > 1 for items in (routes, out, back)):
        raise RuntimeError("duplicate managed RouterOS route or filter")
    return peer, routes, out, back


def reconcile(router, action):
    peer, routes, out, back = state(router)
    if action == "inspect":
        return {"peerAllowsExit": peer["allowed-address"] == EXIT_ALLOWED,
                "route": bool(routes), "forward": bool(out), "return": bool(back)}
    if action == "stage":
        if peer["allowed-address"] == BASE_ALLOWED:
            router.update("/interface/wireguard/peers", peer[".id"],
                          {"allowed-address": EXIT_ALLOWED})
        if not routes:
            router.create("/ip/route", {"dst-address": EXIT, "gateway": ROUTER_PEER,
                                         "distance": "25", "comment": ROUTE_COMMENT})
        for comment, existing, body in (
            (OUT_COMMENT, out, {"in-interface": "wg-cp-mubao", "out-interface": "wg-cp-a1",
                                "src-address": SOURCE, "dst-address": "10.250.0.220",
                                "protocol": "tcp", "dst-port": "1080"}),
            (BACK_COMMENT, back, {"in-interface": "wg-cp-a1", "out-interface": "wg-cp-mubao",
                                  "src-address": "10.250.0.220", "dst-address": SOURCE,
                                  "protocol": "tcp", "src-port": "1080"}),
        ):
            if not existing:
                created = router.create("/ip/firewall/filter", {
                    "chain": "forward", "action": "accept", "comment": comment, **body})
                fasttrack = one(router.rows("/ip/firewall/filter"), "defconf: fasttrack")
                router.move_before("/ip/firewall/filter", created[".id"], fasttrack[".id"])
    else:
        for row in out + back:
            router.delete("/ip/firewall/filter", row[".id"])
        for row in routes:
            router.delete("/ip/route", row[".id"])
        if peer["allowed-address"] == EXIT_ALLOWED:
            router.update("/interface/wireguard/peers", peer[".id"],
                          {"allowed-address": BASE_ALLOWED})
    return reconcile(router, "inspect")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--action", required=True, choices=("inspect", "stage", "rollback"))
    args = parser.parse_args()
    desired = inventory(args.inventory)
    links = {link["node"]: link for link in desired["links"]}
    if links["mubao-wsl"].get("egressRoutes") != [EXIT]:
        raise RuntimeError("unreviewed MUBAO egress route")
    if desired["publicEndpoint"] != "223.159.210.222":
        raise RuntimeError("public authority changed")
    print(json.dumps(reconcile(RouterOS(desired["managementIp"]), args.action), sort_keys=True))


if __name__ == "__main__":
    main()
