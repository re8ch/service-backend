#!/usr/bin/env python3
"""Validate the inert RE8CH Cilium service-VIP Stage 0 boundary."""

import ipaddress
import json
from pathlib import Path
import sys

import yaml


ROOT = Path(__file__).resolve().parent
REQUIRED_LABELS = {
    "networking.re8ch.com/lb-pool": "service-vip",
    "networking.re8ch.com/bgp-advertise": "enabled",
}


def load_yaml(name):
    with (ROOT / name).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def fail(message):
    raise ValueError(message)


def validate_domains(config):
    if config.get("stage") != 0 or config.get("applyEnabled") is not False:
        fail("Stage 0 must remain apply-disabled")
    domains = config["domains"]
    networks = {name: ipaddress.ip_network(value["prefix"]) for name, value in domains.items()}
    names = list(networks)
    for index, left_name in enumerate(names):
        for right_name in names[index + 1 :]:
            if networks[left_name].overlaps(networks[right_name]):
                fail(f"authority domains overlap: {left_name} and {right_name}")
    if domains["nodeIdentity"]["frrOptimizationAllowed"]:
        fail("Node InternalIP optimization must remain disabled")
    if domains["pod"]["frrOptimizationAllowed"]:
        fail("PodCIDR optimization must remain disabled")
    if domains["serviceVip"].get("advertisementPrefixLength") != 32:
        fail("Service VIP advertisements must remain exact /32 routes")
    protected = set(config["controlPlane"]["protectedTcpPorts"])
    if protected != {2379, 2380, 6443, 9345, 10250}:
        fail("protected control-plane TCP port set drifted")
    return networks


def validate_pool(pool, service_network):
    if pool.get("apiVersion") != "cilium.io/v2" or pool.get("kind") != "CiliumLoadBalancerIPPool":
        fail("LB IPAM pool must use cilium.io/v2 CiliumLoadBalancerIPPool")
    spec = pool["spec"]
    if spec.get("disabled") is not True:
        fail("Stage 0 LB IPAM pool must remain disabled")
    blocks = spec.get("blocks", [])
    if blocks != [{"cidr": str(service_network)}]:
        fail("LB IPAM pool must contain only the service VIP domain")
    if spec.get("serviceSelector", {}).get("matchLabels") != REQUIRED_LABELS:
        fail("LB IPAM pool must require both explicit opt-in labels")


def validate_advertisement(advertisement):
    if advertisement.get("apiVersion") != "cilium.io/v2" or advertisement.get("kind") != "CiliumBGPAdvertisement":
        fail("advertisement must use cilium.io/v2 CiliumBGPAdvertisement")
    adverts = advertisement.get("spec", {}).get("advertisements", [])
    if len(adverts) != 1:
        fail("exactly one service advertisement is allowed")
    item = adverts[0]
    if item.get("advertisementType") != "Service":
        fail("only Service advertisements are allowed")
    if item.get("service", {}).get("addresses") != ["LoadBalancerIP"]:
        fail("only LoadBalancerIP advertisements are allowed")
    if item.get("selector", {}).get("matchLabels") != REQUIRED_LABELS:
        fail("BGP advertisement must require both explicit opt-in labels")
    if "aggregationLengthIPv4" in item.get("service", {}):
        fail("service VIP aggregation is prohibited")


def validate_frr_policy():
    policy = (ROOT / "frr-service-vip-policy.conf").read_text(encoding="utf-8")
    required = (
        "permit 192.0.2.215/24 ge 32 le 32",
        "permit 192.0.2.253/24 le 32",
        "permit 192.0.2.224/16 le 32",
        "route-map RE8CH-SVC-VIP-IN deny 1000",
        "route-map RE8CH-SVC-VIP-OUT deny 1000",
    )
    for statement in required:
        if statement not in policy:
            fail(f"FRR policy is missing required guard: {statement}")
    forbidden = ("0.0.0.0/0", "192.0.2.253/24 ge", "192.0.2.224/16 ge")
    for statement in forbidden:
        if statement in policy:
            fail(f"Stage 0 FRR policy contains forbidden statement: {statement}")
    if any(line.strip().startswith("neighbor ") for line in policy.splitlines()):
        fail("Stage 0 FRR policy must not define a neighbor")


def main():
    with (ROOT / "address-domains.json").open(encoding="utf-8") as handle:
        config = json.load(handle)
    networks = validate_domains(config)
    validate_pool(load_yaml("lb-ipam-disabled.yaml"), networks["serviceVip"])
    validate_advertisement(load_yaml("service-advertisement.yaml"))
    validate_frr_policy()
    print("service-VIP Stage 0 valid: inert pool, disjoint domains, exact opt-in LoadBalancer /32 advertisements")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
