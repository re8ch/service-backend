#!/usr/bin/env python3
"""Converge the three WSL PodCIDRs over their existing RouterOS WG links.

Run router actions on a control node with RouterOS REST access, and node
actions locally on each WSL host. The original wg-quick configs are untouched.
"""

import argparse
import ipaddress
import json
import os
import pathlib
import shutil
import subprocess

from control_plane_mesh import RouterOS, comments, ensure


PODS = {
    "restricted-node-1eb0570f74": "192.0.2.109/24",
    "restricted-node-296f36f2fb": "192.0.2.26/24",
    "restricted-node-abb3f27a69": "192.0.2.214/24",
}
ROUTE_SUFFIX = " PodCIDR via WireGuard"
RULE_SUFFIX = " PodCIDR forward "
def fallback_rule_priorities(pod, base):
    octet = ipaddress.ip_network(pod).network_address.packed[2]
    return base + 2 * octet, base + 1 + 2 * octet


def load_inventory(path):
    data = json.loads(pathlib.Path(path).read_text())
    if data.get("authority") != "RouterOS" or data.get("managementIp") != "192.0.2.131":
        raise ValueError("unexpected RouterOS authority")
    links = {link["node"]: link for link in data["links"]}
    if set(links) != set(PODS):
        raise ValueError("expected three WSL control nodes")
    for name, link in links.items():
        if link.get("podCIDR") != PODS[name]:
            raise ValueError(f"unexpected PodCIDR for {name}")
        if ipaddress.ip_network(link["peerAddress"]).prefixlen != 32:
            raise ValueError("peer tunnel address must be /32")
    policy = data["podRoutePolicy"]
    if not 10000 <= int(policy["ruleBase"]) <= 20000 or not 10000 <= int(policy["fallbackTable"]) <= 20000:
        raise ValueError("unreviewed PodCIDR policy rule/table")
    pairs = [tuple(pair) for pair in policy["directPairs"]]
    if len(pairs) != len(set(pairs)) or any(a == b or a not in links or b not in links for a, b in pairs):
        raise ValueError("invalid PodCIDR directPairs")
    return data, links


def run(*cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()


def allowed_with_pod(current, pod):
    entries = current.split(",")
    if pod not in entries:
        entries.append(pod)
    return ",".join(entries)


def router_stage(router, data, links):
    peers = comments(router.rows("/interface/wireguard/peers"))
    routes = comments(router.rows("/ip/route"))
    filters = comments(router.rows("/ip/firewall/filter"))
    anchor = filters.get("defconf: fasttrack")
    if not anchor:
        raise RuntimeError("forward firewall anchor changed")
    # Do not replace unrelated routes; these three exact prefixes are owned here.
    for name, link in links.items():
        route_name = data["commentPrefix"] + " " + name + ROUTE_SUFFIX
        existing = [row for row in router.rows("/ip/route")
                    if row.get("dst-address") == link["podCIDR"]
                    and row.get("comment") != route_name]
        if any(row.get("distance") not in ("20", "250") for row in existing):
            raise RuntimeError(f"unreviewed competing route for {name}")
        peer_name = data["commentPrefix"] + " " + name + " peer"
        peer = peers.get(peer_name)
        if not peer or peer.get("interface") != link["interface"]:
            raise RuntimeError(f"missing reviewed WireGuard peer for {name}")
    for name, link in links.items():
        peer_name = data["commentPrefix"] + " " + name + " peer"
        peer = peers[peer_name]
        wanted = allowed_with_pod(peer["allowed-address"], link["podCIDR"])
        if wanted != peer["allowed-address"]:
            router.update("/interface/wireguard/peers", peer[".id"], {"allowed-address": wanted})
        route_name = data["commentPrefix"] + " " + name + ROUTE_SUFFIX
        next_hop = str(ipaddress.ip_network(link["peerAddress"]).network_address)
        ensure(router, "/ip/route", route_name, {
            "dst-address": link["podCIDR"], "gateway": next_hop,
            "distance": "10", "disabled": "false",
        })
    # Include both Pod and node identity sources. API-server webhook traffic
    # originates from the node identity, while workloads use Pod IPs.
    for source_name, source in links.items():
        for target_name, target in links.items():
            if source_name == target_name:
                continue
            for source_kind, source_prefix in (("pod", source["podCIDR"]),
                                               ("host", source["identity"])):
                name = (data["commentPrefix"] + " " + source_name + RULE_SUFFIX
                        + target_name + " " + source_kind)
                rule = ensure(router, "/ip/firewall/filter", name, {
                    "chain": "forward", "action": "accept", "disabled": "false",
                    "in-interface": source["interface"],
                    "out-interface": target["interface"],
                    "src-address": source_prefix, "dst-address": target["podCIDR"],
                })
                router.move_before("/ip/firewall/filter", rule[".id"], anchor[".id"])
    return {"routerPodRoutes": sorted(link["podCIDR"] for link in links.values()),
            "forwardRules": 2 * len(links) * (len(links) - 1)}


def router_rollback(router, data, links):
    prefix = data["commentPrefix"] + " "
    route_names = {prefix + name + ROUTE_SUFFIX for name in links}
    rule_names = {prefix + source + RULE_SUFFIX + target + " " + kind
                  for source in links for target in links if source != target
                  for kind in ("pod", "host")}
    for row in router.rows("/ip/firewall/filter"):
        if row.get("comment") in rule_names:
            router.delete("/ip/firewall/filter", row[".id"])
    for row in router.rows("/ip/route"):
        if row.get("comment") in route_names:
            router.delete("/ip/route", row[".id"])
    peers = comments(router.rows("/interface/wireguard/peers"))
    for name, link in links.items():
        peer = peers[prefix + name + " peer"]
        entries = [entry for entry in peer["allowed-address"].split(",") if entry != link["podCIDR"]]
        router.update("/interface/wireguard/peers", peer[".id"], {"allowed-address": ",".join(entries)})
    return {"rolledBack": True}


def node_action(action, node, links, policy):
    own = links[node]
    interface = own["interface"]
    router_ip = str(ipaddress.ip_interface(own["routerAddress"]).ip)
    try:
        original = run("wg", "show", interface, "allowed-ips").split()
    except subprocess.CalledProcessError:
        if action == "stage-node":
            raise
        original = []  # wg-quick may have stopped before this unit's ExecStop.
    if original and (len(original) < 2 or original[0] != own["routerPublicKey"]):
        raise RuntimeError("unexpected local WireGuard peer")
    base_allowed = original[1:]
    remote = [(name, link["podCIDR"]) for name, link in links.items() if name != node]
    remote_pods = [pod for _, pod in remote]
    transit_sources = policy.get("transitSources", [])
    for prefix in transit_sources:
        network = ipaddress.ip_network(prefix)
        if not any(network.subnet_of(ipaddress.ip_network(root)) for root in
                   ("192.0.2.106/24", "192.0.2.224/16")):
            raise ValueError("transit source outside fabric authority")
    managed_sources = remote_pods + transit_sources
    direct_pairs = {tuple(pair) for pair in policy["directPairs"]}
    fallback_table = str(policy["fallbackTable"])
    if action == "stage-node":
        allowed = list(dict.fromkeys(base_allowed + managed_sources))
    else:
        allowed = [item for item in base_allowed if item not in managed_sources]
    if original:
        run("wg", "set", interface, "peer", own["routerPublicKey"], "allowed-ips", ",".join(allowed))
    for peer_name, pod in remote:
        direct_priority, fallback_priority = fallback_rule_priorities(pod, int(policy["ruleBase"]))
        if (node, peer_name) not in direct_pairs:
            # MUBAO's alternative FRR paths currently reach the PodCIDRs
            # through restricted-node-6dc3f140f9, but their cross-node forwarding is not healthy.
            # Keep these four verified RouterOS paths until the underlying
            # forwarding and return paths pass the remote DNS probes.
            subprocess.run(("ip", "rule", "del", "pref", str(direct_priority),
                            "to", pod, "lookup", "main", "suppress_prefixlength", "23"),
                           check=False, capture_output=True)
            subprocess.run(("ip", "rule", "del", "pref", str(fallback_priority),
                            "to", pod, "lookup", fallback_table),
                           check=False, capture_output=True)
            subprocess.run(("ip", "route", "del", pod, "table", fallback_table),
                           check=False, capture_output=True)
            if action == "stage-node":
                run("ip", "route", "replace", pod, "via", router_ip, "dev", interface,
                    "src", own["identity"].split("/")[0], "metric", "10")
            else:
                subprocess.run(("ip", "route", "del", pod, "via", router_ip,
                                "dev", interface), check=False, capture_output=True)
            continue
        if action == "stage-node":
            # A metric-10 RouterOS route in main hides the FRR /24 even when
            # BGP is healthy. Consult main for a specific route first, then
            # use the RouterOS route only when that route disappears.
            run("ip", "route", "replace", pod, "via", router_ip, "dev", interface,
                "src", own["identity"].split("/")[0], "metric", "250",
                "table", fallback_table)
            subprocess.run(("ip", "rule", "del", "pref", str(direct_priority),
                            "to", pod, "lookup", "main", "suppress_prefixlength", "23"),
                           check=False, capture_output=True)
            subprocess.run(("ip", "rule", "del", "pref", str(fallback_priority),
                            "to", pod, "lookup", fallback_table),
                           check=False, capture_output=True)
            run("ip", "rule", "add", "pref", str(direct_priority), "to", pod,
                "lookup", "main", "suppress_prefixlength", "23")
            run("ip", "rule", "add", "pref", str(fallback_priority), "to", pod,
                "lookup", fallback_table)
            subprocess.run(("ip", "route", "del", pod, "via", router_ip,
                            "dev", interface), check=False, capture_output=True)
        else:
            subprocess.run(("ip", "rule", "del", "pref", str(direct_priority),
                            "to", pod, "lookup", "main", "suppress_prefixlength", "23"),
                           check=False, capture_output=True)
            subprocess.run(("ip", "rule", "del", "pref", str(fallback_priority),
                            "to", pod, "lookup", fallback_table),
                           check=False, capture_output=True)
            subprocess.run(("ip", "route", "del", pod, "table", fallback_table),
                           check=False, capture_output=True)
            subprocess.run(("ip", "route", "del", pod, "via", router_ip, "dev", interface),
                           check=False, capture_output=True)
    return {"node": node, "action": action, "remotePodCIDRs": remote_pods}


def render_unit(node, interface):
    command = ("/usr/bin/python3 /etc/re8ch/control-pod-mesh/control_pod_mesh.py "
               "--inventory /etc/re8ch/control-pod-mesh/routeros-control-mesh.json "
               f"--node {node}")
    wireguard = f"wg-quick@{interface}.service"
    return ("[Unit]\n"
            f"Description=RE8CH symmetric Pod routes for {node}\n"
            f"Requires={wireguard}\nAfter={wireguard}\nBindsTo={wireguard}\n"
            f"PartOf={wireguard}\n\n"
            "[Service]\nType=oneshot\nRemainAfterExit=yes\n"
            f"ExecStart={command} --action stage-node\n"
            f"ExecStop={command} --action rollback-node\n\n"
            "[Install]\nWantedBy=multi-user.target\n"
            f"WantedBy={wireguard}\n")


def install_node(node, links, inventory_path):
    if os.geteuid() != 0:
        raise PermissionError("install-node requires root")
    interface = links[node]["interface"]
    if not pathlib.Path(f"/etc/wireguard/{interface}.conf").is_file():
        raise RuntimeError("reviewed wg-quick interface is not installed")
    source = pathlib.Path(__file__).resolve().parent
    target = pathlib.Path("/etc/re8ch/control-pod-mesh")
    target.mkdir(parents=True, exist_ok=True)
    for name, original in (
        ("control_pod_mesh.py", pathlib.Path(__file__)),
        ("control_plane_mesh.py", source / "control_plane_mesh.py"),
        ("routeros-control-mesh.json", pathlib.Path(inventory_path)),
    ):
        staged = target / (name + ".new")
        shutil.copyfile(original, staged)
        os.chmod(staged, 0o644)
        os.replace(staged, target / name)
    unit = pathlib.Path("/etc/systemd/system/re8ch-control-pod-mesh.service")
    staged = unit.with_suffix(".service.new")
    staged.write_text(render_unit(node, interface))
    os.chmod(staged, 0o644)
    os.replace(staged, unit)
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", "--now", unit.name)
    return {"node": node, "service": run("systemctl", "is-active", unit.name)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--action", required=True,
                        choices=("validate", "stage-router", "rollback-router", "stage-node",
                                 "rollback-node", "install-node"))
    parser.add_argument("--node", choices=tuple(PODS))
    parser.add_argument("--nodes", default=",".join(PODS),
                        help="comma-separated canary set; defaults to all three")
    args = parser.parse_args()
    data, all_links = load_inventory(args.inventory)
    selected = args.nodes.split(",")
    if len(selected) != len(set(selected)) or not set(selected).issubset(PODS) or len(selected) < 2:
        parser.error("--nodes must contain two or three distinct reviewed control nodes")
    links = {name: all_links[name] for name in selected}
    if args.action == "validate":
        result = {name: {"podCIDR": link["podCIDR"], "interface": link["interface"]}
                  for name, link in links.items()}
    elif args.action.endswith("node"):
        if not args.node or args.node not in links:
            parser.error("--node must belong to --nodes for node actions")
        result = (install_node(args.node, links, args.inventory) if args.action == "install-node"
                  else node_action(args.action, args.node, links, data["podRoutePolicy"]))
    else:
        if args.node:
            parser.error("--node is only valid for node actions")
        router = RouterOS(data["managementIp"], "https://192.0.2.114:6443")
        result = (router_stage(router, data, links) if args.action == "stage-router"
                  else router_rollback(router, data, links))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
