#!/usr/bin/env python3
"""Idempotently provision the NetBird Loudi CN / LA overseas split profile."""
import json
import os
import urllib.request

API = os.environ.get("NETBIRD_API_URL", "https://access.re8ch.com:8443").rstrip("/")
TOKEN = os.environ["NETBIRD_API_TOKEN"]
CLIENT_GROUP = "loudi-split-clients"
RESOURCE_GROUP = "loudi-split-ipv4-resources"
ROUTER_GROUP = "mihomo-egress-r640"
NETWORK = "娄底出口+海外请求分流"
SHARDS = ["192.0.2.92/8", "192.0.2.112/7", "192.0.2.38/6", "192.0.2.235/5",
          "192.0.2.111/4", "192.0.2.97/3", "192.0.2.225/2", "192.0.2.139/1"]


def api(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Authorization": f"Token {TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


groups = api("GET", "/api/groups")


def ensure_group(name):
    group = next((item for item in groups if item["name"] == name), None)
    if group:
        return group
    group = api("POST", "/api/groups", {"name": name, "peers": []})
    groups.append(group)
    return group


client_group = ensure_group(CLIENT_GROUP)
resource_group = ensure_group(RESOURCE_GROUP)
router_group = next(item for item in groups if item["name"] == ROUTER_GROUP)

networks = api("GET", "/api/networks")
network = next((item for item in networks if item["name"] == NETWORK), None)
if not network:
    network = api("POST", "/api/networks", {
        "name": NETWORK,
        "description": "CN via Loudi WireGuard direct; non-CN via LA fabric",
    })

resource_path = f"/api/networks/{network['id']}/resources"
resources = api("GET", resource_path) or []
for index, address in enumerate(SHARDS, 1):
    if any(item["address"] == address for item in resources):
        continue
    api("POST", resource_path, {
        "name": f"Loudi split IPv4 {index}/8",
        "description": "Full IPv4 enters restricted-node-6dc3f140f9 GeoDB classifier",
        "address": address,
        "enabled": True,
        "groups": [resource_group["id"]],
    })

router_path = f"/api/networks/{network['id']}/routers"
routers = api("GET", router_path) or []
if not any(router_group["id"] in item.get("peer_groups", []) for item in routers):
    api("POST", router_path, {
        "peer": "",
        "peer_groups": [router_group["id"]],
        "metric": 10,
        "masquerade": True,
        "enabled": True,
    })

policies = api("GET", "/api/policies")
if not any(item["name"] == NETWORK for item in policies):
    api("POST", "/api/policies", {
        "name": NETWORK,
        "description": "Client profile: CN exits Loudi, non-CN exits LA",
        "enabled": True,
        "rules": [{
            "name": NETWORK,
            "description": "Route profile clients through the restricted-node-6dc3f140f9 GeoDB classifier",
            "enabled": True,
            "action": "accept",
            "bidirectional": False,
            "protocol": "all",
            "sources": [client_group["id"]],
            "destinations": [resource_group["id"]],
        }],
    })

print(json.dumps({
    "network": NETWORK,
    "client_group": CLIENT_GROUP,
    "resource_group": RESOURCE_GROUP,
    "router_group": ROUTER_GROUP,
    "ipv4_shards": len(SHARDS),
}, ensure_ascii=False, sort_keys=True))
