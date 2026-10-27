#!/usr/bin/env python3
"""Fail closed when R640 public Gateway values bypass RouterOS authority."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "operations/network"))
from restricted_topology import load_inventory, verify_public

INVENTORY = ROOT / "operations/network/manifests/network/public-mesh/inventory.json"
AMONG_RELEASE = ROOT / "platform/among-clusters/release.yaml"


def validate(inventory_path: Path = INVENTORY, release_path: Path = AMONG_RELEASE) -> None:
    inventory = load_inventory(inventory_path)
    r640 = next(node for node in inventory["nodes"] if node["name"] == "r640")
    authority = r640.get("publicAuthority") or {}
    expected = {
        "ownership": "provider",
        "sourceRef": "operations/network/manifests/network/public-mesh/inventory.json#r640.publicAuthority",
        "publicIP": r640["publicIp"],
        "deliveryMode": r640["publicMode"],
        "edgeType": authority.get("type"),
        "gatewayVIP": authority.get("gatewayVIP"),
        "gatewayRef": authority.get("gatewayRef"),
        "routingAuthority": authority.get("routingAuthority"),
        "fallbackLanBackend": authority.get("fallbackLanBackend"),
        "requiredForwardPorts": authority.get("requiredForwardPorts"),
        "dnsProtocols": authority.get("dnsProtocols"),
    }
    if expected != {
        "ownership": "provider",
        "sourceRef": "operations/network/manifests/network/public-mesh/inventory.json#r640.publicAuthority",
        "publicIP": "223.159.210.222",
        "deliveryMode": "mikrotik-ebgp-transit-dnat",
        "edgeType": "RouterOS",
        "gatewayVIP": "10.251.0.4",
        "gatewayRef": "kube-system/re8ch-gateway-canary",
        "routingAuthority": "AdvancedFabric",
        "fallbackLanBackend": "192.168.88.251",
        "requiredForwardPorts": [25, 53, 80, 443, 6443, 465, 587, 993, 4190],
        "dnsProtocols": ["udp", "tcp"],
    }:
        raise SystemExit(f"unexpected R640 publicAuthority: {expected}")

    documents = list(yaml.safe_load_all(release_path.read_text()))
    release = next(
        item for item in documents
        if item and item.get("kind") == "HelmRelease" and item["metadata"]["name"] == "among-clusters-agent"
    )
    gateway = release["spec"]["values"]["gateway"]
    if gateway.get("publicAuthority") != expected:
        raise SystemExit(f"AmongClusters must declare canonical R640 publicAuthority: {expected}")
    endpoint = gateway["listener"]["publicEndpoint"]
    host = urlparse(endpoint).hostname
    if host != expected["publicIP"] or host == r640["ztIp"]:
        raise SystemExit(
            f"AmongClusters R640 publicEndpoint must use {expected['publicIP']}, not ZeroTier/dummy {r640['ztIp']}: {endpoint}"
        )
    manifest_paths = sorted(release_path.parent.glob("*.yaml"))
    serialized = "\n".join(path.read_text() for path in manifest_paths)
    forbidden = (
        f'localGateway: quic://{r640["ztIp"]}',
        f'publicEndpoint: "quic://{r640["ztIp"]}',
    )
    if any(item in serialized for item in forbidden):
        raise SystemExit(f"R640 ZeroTier/dummy address {r640['ztIp']} cannot be declared as a public Gateway endpoint")
    for manifest_path in manifest_paths:
        for item in yaml.safe_load_all(manifest_path.read_text()):
            if not item or item.get("kind") != "AuthenticatedLink":
                continue
            link = item.get("spec", {})
            if not str(link.get("peerRef", "")).startswith("re8ch-"):
                continue
            link_host = urlparse(link.get("localGateway", "")).hostname
            if link_host != expected["publicIP"]:
                raise SystemExit(
                    f"{manifest_path.name}/{item['metadata']['name']} must use provider public authority "
                    f"{expected['publicIP']} as localGateway, got {link.get('localGateway', '')}"
                )


if __name__ == "__main__":
    if os.environ.get("NETWORK_TOPOLOGY_BUNDLE") or os.environ.get("NETWORK_TOPOLOGY_FILE"):
        validate()
        print("Restricted public authority validated")
    else:
        verify_public()
        print("Public authority reference verified; semantic checks require protected topology validation")
