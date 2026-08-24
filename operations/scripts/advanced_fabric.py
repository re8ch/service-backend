#!/usr/bin/env python3
"""Advanced Fabric inventory validation, policy resolution and safe rendering.

The controller deliberately separates network truth (ASN, peers and owned
prefixes) from economics preferences.  Economics output can only affect
LOCAL_PREF and supplemental policy-routing tables.
"""

import argparse
import ipaddress
import json
import math
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request


PROFILES = ("fastest", "balanced", "greedy")
DEFAULT_TABLES = {"fastest": 201, "balanced": 202, "greedy": 203}
DEFAULT_MARKS = {"fastest": 42241, "balanced": 42242, "greedy": 42243}
ALLOWED_PREFIXES = (
    ipaddress.ip_network("192.0.2.57/16"),
    ipaddress.ip_network("192.0.2.65/24"),
    ipaddress.ip_network("192.0.2.121/24"),
)
PROTECTED_NODE_CIDR = ipaddress.ip_network("192.0.2.20/24")


def validate_fabric(spec):
    errors = []
    if spec.get("applyEnabled") and spec.get("mode") != "guarded-apply":
        errors.append("applyEnabled requires mode=guarded-apply")
    if spec.get("emergencyDisable") and spec.get("applyEnabled"):
        errors.append("emergencyDisable and applyEnabled are mutually exclusive")
    networks = [ipaddress.ip_network(spec[key]) for key in
                ("nativeRoutingCIDR", "acceleratedCIDR", "serviceCIDR")]
    if networks[0] != ALLOWED_PREFIXES[0]:
        errors.append("nativeRoutingCIDR must remain 192.0.2.57/16")
    if any(a.overlaps(b) for index, a in enumerate(networks) for b in networks[index + 1:]):
        errors.append("fabric address domains overlap")
    names, asns, pods, accelerators = set(), set(), set(), set()
    spine_asns = set(range(65001, 65005)) | set(range(65101, 65104))
    for node in spec.get("nodes", []):
        name, asn = node.get("name"), int(node.get("asn", 0))
        if not name or name in names:
            errors.append(f"duplicate or missing node name: {name}")
        if not asn or asn in asns:
            errors.append(f"duplicate or missing ASN: {name}/{asn}")
        names.add(name); asns.add(asn)
        pod = ipaddress.ip_network(node["podCIDR"])
        accel = ipaddress.ip_address(node["acceleratedIP"])
        internal = ipaddress.ip_address(node["internalIP"])
        if pod.prefixlen != 24 or not pod.subnet_of(networks[0]) or pod in pods:
            errors.append(f"invalid or duplicate PodCIDR: {name}/{pod}")
        if accel not in networks[1] or accel in accelerators:
            errors.append(f"invalid or duplicate accelerated IP: {name}/{accel}")
        if internal not in PROTECTED_NODE_CIDR:
            errors.append(f"InternalIP outside protected identity domain: {name}/{internal}")
        pods.add(pod); accelerators.add(accel)
        role, klass = node.get("role"), node.get("class")
        if (role == "spine") != (klass in ("A", "B")):
            errors.append(f"class/role mismatch: {name}")
        if role == "spine" and asn not in spine_asns:
            errors.append(f"spine ASN outside assigned private range: {name}/{asn}")
        if role == "leaf" and node.get("legacyASN"):
            errors.append(f"leaf ASN must remain stable: {name}")
        if float(node.get("uplinkMbps", 0)) <= 0:
            errors.append(f"missing nominal uplink: {name}")
    if len([node for node in spec.get("nodes", []) if node.get("role") == "spine"]) != 7:
        errors.append("exactly seven static spine members are required")
    if errors:
        raise ValueError("; ".join(errors))
    return True


def quota_pressure(usage_gib, limit_gib):
    if limit_gib in (None, 0):
        return {"ratio": 0.0, "tier": "unlimited", "penalty": 0.0}
    ratio = max(0.0, float(usage_gib) / float(limit_gib))
    if ratio >= 1:
        return {"ratio": ratio, "tier": "exhausted", "penalty": 1000.0}
    if ratio >= .95:
        return {"ratio": ratio, "tier": "critical", "penalty": 400.0}
    if ratio >= .80:
        return {"ratio": ratio, "tier": "warning", "penalty": 120.0}
    # Greedy prefers consuming allowance that would otherwise expire.
    return {"ratio": ratio, "tier": "normal", "penalty": -100.0 * ratio}


def score_path(profile, facts, node):
    """Return a lower-is-better score without changing network truth."""
    if not facts.get("bgp", False) or not facts.get("bfd", False) or not facts.get("nodeReady", False):
        return math.inf
    if not node.get("inventoryComplete", False):
        return math.inf
    loss = float(facts.get("loss", 1.0))
    if loss > .05:
        return math.inf
    rtt = float(facts.get("rttMs", 10000))
    jitter = float(facts.get("jitterMs", 1000))
    throughput = max(0.001, min(float(facts.get("throughputMbps", 0.001)), float(node["uplinkMbps"])))
    pressure = quota_pressure(facts.get("monthlyUsageGiB", 0), node.get("monthlyTrafficLimitGiB"))
    risk = float(facts.get("failureDomainRisk", 0))
    if profile == "fastest":
        return rtt + 4 * jitter + 4000 * loss - 2 * math.log2(throughput) + max(0, pressure["penalty"] - 400)
    if profile == "greedy":
        return pressure["penalty"] + .25 * rtt + 1000 * loss + 2 * risk - math.log2(throughput)
    return .6 * rtt + 2 * jitter + 2500 * loss + .5 * pressure["penalty"] + 5 * risk - 1.5 * math.log2(throughput)


def eligible_paths(profile, candidates):
    ranked = []
    for item in candidates:
        score = score_path(profile, item.get("facts", {}), item["node"])
        if math.isfinite(score):
            ranked.append({**item, "score": score})
    return sorted(ranked, key=lambda item: (item["score"], item["node"]["name"]))


def policy_sort_key(policy, cidr):
    spec = policy.get("spec", {})
    return (-int(spec.get("priority", 100)), -ipaddress.ip_network(cidr).prefixlen,
            policy.get("metadata", {}).get("name", ""))


def resolve_policy(policies, pod_namespace, pod_labels, destination):
    address = ipaddress.ip_address(destination)
    matches = []
    for policy in policies:
        meta, spec = policy.get("metadata", {}), policy.get("spec", {})
        if meta.get("namespace") != pod_namespace:
            continue
        selector = spec.get("podSelector", {}).get("matchLabels", {})
        if any(pod_labels.get(key) != value for key, value in selector.items()):
            continue
        cidrs = spec.get("destination", {}).get("cidrs", [])
        if spec.get("destination", {}).get("class") == "cluster":
            cidrs += [str(network) for network in ALLOWED_PREFIXES]
        elif spec.get("destination", {}).get("class") == "public-internet":
            cidrs += ["0.0.0.0/0"]
        for cidr in cidrs:
            if address in ipaddress.ip_network(cidr):
                matches.append((policy_sort_key(policy, cidr), spec.get("profile", "balanced"), cidr, policy))
    return min(matches, key=lambda item: item[0]) if matches else (None, "balanced", None, None)


def render_frr(node, fabric, phase="final"):
    """Render a namespaced FRR fragment; never emits a default or NodeIP route."""
    spines = [item for item in fabric["nodes"] if item["role"] == "spine" and item["name"] != node["name"]]
    lines = [
        "! BEGIN RE8CH ADVANCED FABRIC",
        "ip prefix-list AF-IN seq 10 permit 192.0.2.57/16 ge 24 le 24",
        "ip prefix-list AF-IN seq 20 permit 192.0.2.65/24 ge 32 le 32",
        "ip prefix-list AF-IN seq 30 permit 192.0.2.121/24 ge 32 le 32",
        "ip prefix-list AF-IN seq 1000 deny any",
        f"ip prefix-list AF-SELF seq 10 permit {node['podCIDR']}",
        f"ip prefix-list AF-SELF seq 20 permit {node['acceleratedIP']}/32",
        "ip prefix-list AF-SELF seq 1000 deny any",
        f"router bgp {node.get('legacyASN') if phase == 'compatibility' and node.get('legacyASN') else node['asn']}",
        " bgp ebgp-requires-policy",
        " bgp bestpath as-path multipath-relax",
        " no bgp default ipv4-unicast",
    ]
    for peer in spines:
        remote_as = "external" if phase == "compatibility" else str(peer["asn"])
        lines += [
            f" neighbor {peer['acceleratedIP']} remote-as {remote_as}",
            f" neighbor {peer['acceleratedIP']} timers 3 9",
            f" neighbor {peer['acceleratedIP']} bfd",
        ]
        if phase == "compatibility" and node.get("legacyASN"):
            lines.append(f" neighbor {peer['acceleratedIP']} local-as {node['asn']} no-prepend replace-as dual-as")
    lines += [" address-family ipv4 unicast", f"  network {node['podCIDR']}",
              f"  network {node['acceleratedIP']}/32"]
    for peer in spines:
        lines += [f"  neighbor {peer['acceleratedIP']} activate",
                  f"  neighbor {peer['acceleratedIP']} prefix-list AF-IN in",
                  f"  neighbor {peer['acceleratedIP']} prefix-list {'AF-IN' if node['role'] == 'spine' else 'AF-SELF'} out",
                  f"  neighbor {peer['acceleratedIP']} maximum-prefix 64 90 restart 5"]
    lines += ["  maximum-paths 7", " exit-address-family", "! END RE8CH ADVANCED FABRIC"]
    output = "\n".join(lines) + "\n"
    if "0.0.0.0/0" in output or "192.0.2.20/24" in output:
        raise ValueError("renderer attempted to emit a forbidden prefix")
    return output


def render_policy_bundle(pod_profiles, routes, tables=None, marks=None, weighted=False):
    tables, marks = tables or DEFAULT_TABLES, marks or DEFAULT_MARKS
    nft = ["table inet advanced_fabric {", " chain mark_pods {", "  type filter hook prerouting priority mangle; policy accept;"]
    for profile in PROFILES:
        ips = sorted(pod_profiles.get(profile, []), key=ipaddress.ip_address)
        if ips:
            nft.append(f"  ip saddr {{ {', '.join(ips)} }} meta mark set {marks[profile]}")
    nft += [" }", "}"]
    ip_rules = [f"ip rule replace priority {11000 + index} fwmark {marks[p]} lookup {tables[p]}"
                for index, p in enumerate(PROFILES)]
    ip_routes = []
    for profile in PROFILES:
        for prefix, next_hops in sorted(routes.get(profile, {}).items()):
            network = ipaddress.ip_network(prefix)
            if network == ipaddress.ip_network("0.0.0.0/0"):
                # A policy-table default is permitted but never redistributed by FRR.
                pass
            elif not any(network.subnet_of(allowed) for allowed in ALLOWED_PREFIXES):
                raise ValueError(f"policy route outside authorized domains: {prefix}")
            hops = next_hops if weighted else [{**hop, "weight": 1} for hop in next_hops]
            nh = " ".join(f"nexthop via {hop['via']} dev {hop['dev']} weight {int(hop.get('weight', 1))}" for hop in hops)
            ip_routes.append(f"ip route replace table {tables[profile]} {prefix} {nh}")
    return {"nftables": "\n".join(nft) + "\n", "ipRules": ip_rules, "ipRoutes": ip_routes}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fabric", required=True)
    parser.add_argument("command", choices=("validate", "render-frr"))
    parser.add_argument("--node")
    parser.add_argument("--phase", choices=("compatibility", "final"), default="final")
    args = parser.parse_args()
    with open(args.fabric, encoding="utf-8") as handle:
        document = json.load(handle)
    spec = document.get("spec", document)
    validate_fabric(spec)
    if args.command == "validate":
        print(json.dumps({"nodes": len(spec["nodes"]), "spines": 7, "valid": True}, sort_keys=True))
    else:
        if not args.node:
            parser.error("--node is required for render-frr")
        node = next(item for item in spec["nodes"] if item["name"] == args.node)
        print(render_frr(node, spec, args.phase), end="")


if __name__ == "__main__":
    main()
