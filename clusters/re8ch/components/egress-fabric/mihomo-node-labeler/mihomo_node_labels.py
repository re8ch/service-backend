#!/usr/bin/env python3
"""Enable Mihomos consumers on Ready Kubernetes nodes and public global exits."""

import base64
import ipaddress
import json
import os
import ssl
import time
import urllib.request
from pathlib import Path


SERVICE_ACCOUNT = Path("/var/run/secrets/kubernetes.io/serviceaccount")
INVENTORY_PATH = "/api/v1/namespaces/flux-system/secrets/cluster-node-inventory"


def api(path, method="GET", payload=None):
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
    token = (SERVICE_ACCOUNT / "token").read_text().strip()
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        f"https://{host}:{port}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/merge-patch+json"},
    )
    context = ssl.create_default_context(cafile=str(SERVICE_ACCOUNT / "ca.crt"))
    with urllib.request.urlopen(request, context=context, timeout=10) as response:
        return json.load(response) if response.status != 204 else {}


def public_ipv4(node):
    for item in node.get("status", {}).get("addresses", []):
        if item.get("type") != "ExternalIP":
            continue
        try:
            address = ipaddress.ip_address(item.get("address", ""))
        except ValueError:
            continue
        if address.version == 4 and address.is_global:
            return str(address)
    return ""


def desired_labels(node, inventory):
    name = node.get("metadata", {}).get("name", "")
    conditions = node.get("status", {}).get("conditions", [])
    if not any(item.get("type") == "Ready" and item.get("status") == "True"
               for item in conditions):
        return {}
    labels = node.get("metadata", {}).get("labels", {})
    region = labels.get("topology.kubernetes.io/region", "")
    if not region or region.endswith("unverified"):
        region = inventory.get(name, {}).get("region", "")
    if not region or region.endswith("unverified"):
        return {}
    desired = {"networking.re8ch.com/mihomo-node": "enabled",
               "networking.re8ch.com/geo": "cn" if region.startswith("cn-") else "global"}
    if desired["networking.re8ch.com/geo"] == "global" and public_ipv4(node):
        desired["networking.re8ch.com/mihomo-exit"] = "enabled"
    return desired


def reconcile():
    nodes = api("/api/v1/nodes").get("items", [])
    try:
        inventory = json.loads(base64.b64decode(api(INVENTORY_PATH)["data"]["nodes.json"]))
    except (KeyError, ValueError, urllib.error.HTTPError, urllib.error.URLError) as exc:
        print(json.dumps({"level": "warning", "event": "mihomo-inventory-facts-unavailable",
                          "error": type(exc).__name__}), flush=True)
        inventory = {}
    patched = []
    for node in nodes:
        name = node.get("metadata", {}).get("name", "")
        desired = desired_labels(node, inventory)
        current = node.get("metadata", {}).get("labels", {})
        managed = ("networking.re8ch.com/mihomo-node", "networking.re8ch.com/geo",
                   "networking.re8ch.com/mihomo-exit")
        changed = {key: desired.get(key) for key in managed
                   if (key in desired and current.get(key) != desired[key])
                   or (key not in desired and key in current)}
        if changed:
            api(f"/api/v1/nodes/{name}", "PATCH", {"metadata": {"labels": changed}})
            patched.append(name)
    print(json.dumps({"observedNodes": len(nodes), "inventoryFacts": len(inventory),
                      "patched": patched}), flush=True)


if __name__ == "__main__":
    while True:
        try:
            reconcile()
        except Exception as exc:
            print(json.dumps({"level": "error", "event": "mihomo-node-label-reconcile-failed",
                              "error": type(exc).__name__}), flush=True)
        time.sleep(60)
