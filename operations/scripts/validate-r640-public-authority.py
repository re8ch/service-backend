#!/usr/bin/env python3
"""Fail closed when restricted-node-6dc3f140f9 public Gateway values bypass RouterOS authority."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import yaml


ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "operations/network/manifests/network/public-mesh/inventory.json"
AMONG_RELEASE = ROOT / "platform/among-clusters/release.yaml"


def validate(inventory_path: Path = INVENTORY, release_path: Path = AMONG_RELEASE) -> None:
    inventory = json.loads(inventory_path.read_text())
    restricted-node-6dc3f140f9 = next(node for node in inventory["nodes"] if node["name"] == "restricted-node-6dc3f140f9")
    authority = restricted-node-6dc3f140f9.get("publicAuthority") or {}
    expected = {
        "ownership": "provider",
        "sourceRef": "operations/network/manifests/network/public-mesh/inventory.json#restricted-node-6dc3f140f9.publicAuthority",
        "publicIP": restricted-node-6dc3f140f9["publicIp"],
        "deliveryMode": restricted-node-6dc3f140f9["publicMode"],
        "edgeType": authority.get("type"),
        "lanBackend": authority.get("lanBackend"),
    }
    if expected != {
        "ownership": "provider",
        "sourceRef": "operations/network/manifests/network/public-mesh/inventory.json#restricted-node-6dc3f140f9.publicAuthority",
        "publicIP": "192.0.2.138",
        "deliveryMode": "mikrotik-ebgp-transit-dnat",
        "edgeType": "RouterOS",
        "lanBackend": "192.0.2.169",
    }:
        raise SystemExit(f"unexpected restricted-node-6dc3f140f9 publicAuthority: {expected}")

    documents = list(yaml.safe_load_all(release_path.read_text()))
    release = next(
        item for item in documents
        if item and item.get("kind") == "HelmRelease" and item["metadata"]["name"] == "among-clusters-agent"
    )
    gateway = release["spec"]["values"]["gateway"]
    if gateway.get("publicAuthority") != expected:
        raise SystemExit(f"AmongClusters must declare canonical restricted-node-6dc3f140f9 publicAuthority: {expected}")
    endpoint = gateway["listener"]["publicEndpoint"]
    host = urlparse(endpoint).hostname
    if host != expected["publicIP"] or host == restricted-node-6dc3f140f9["ztIp"]:
        raise SystemExit(
            f"AmongClusters restricted-node-6dc3f140f9 publicEndpoint must use {expected['publicIP']}, not ZeroTier/dummy {restricted-node-6dc3f140f9['ztIp']}: {endpoint}"
        )
    manifest_paths = sorted(release_path.parent.glob("*.yaml"))
    serialized = "\n".join(path.read_text() for path in manifest_paths)
    forbidden = (
        f'localGateway: quic://{restricted-node-6dc3f140f9["ztIp"]}',
        f'publicEndpoint: "quic://{restricted-node-6dc3f140f9["ztIp"]}',
    )
    if any(item in serialized for item in forbidden):
        raise SystemExit(f"restricted-node-6dc3f140f9 ZeroTier/dummy address {restricted-node-6dc3f140f9['ztIp']} cannot be declared as a public Gateway endpoint")
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
    validate()
    print("restricted-node-6dc3f140f9 public authority validated")
