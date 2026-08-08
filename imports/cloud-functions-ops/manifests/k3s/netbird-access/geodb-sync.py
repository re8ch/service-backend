#!/usr/bin/env python3
"""Compile GeoSite category YAML into client-local NetBird domain resources."""

import hashlib
import json
import os
import re
import sys
import urllib.request

API = os.environ.get("NETBIRD_API_URL", "https://access.re8ch.com:8443").rstrip("/")
TOKEN = os.environ["NETBIRD_API_TOKEN"]
NETWORK_NAME = os.environ.get("NETBIRD_NETWORK_NAME", "GeoDB Overseas via LA")
RESOURCE_GROUP = os.environ.get("NETBIRD_RESOURCE_GROUP", "geodb-overseas-resources")
CATEGORIES = [x.strip() for x in os.environ.get(
    "GEOSITE_CATEGORIES", "google,youtube,openai,github,telegram,twitter,netflix"
).split(",") if x.strip()]
SOURCE = os.environ.get(
    "GEOSITE_URL_TEMPLATE",
    "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/{category}.yaml",
)
PRUNE = os.environ.get("GEODB_PRUNE", "false").lower() == "true"
MAX_RESOURCES = int(os.environ.get("GEODB_MAX_RESOURCES", "1200"))
ROOT_DOMAINS = {x.strip().lower() for x in os.environ.get(
    "GEODB_ROOT_DOMAINS",
    "google.com,youtube.com,openai.com,github.com,telegram.org,twitter.com,x.com,netflix.com",
).split(",") if x.strip()}
DOMAIN_RE = re.compile(r"^(?:\+\.)?([a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?)$", re.I)
COMMON_CC_SLD = {"ac", "co", "com", "edu", "gov", "net", "org"}


def api(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={"Authorization": f"Token {TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read()
    return json.loads(payload) if payload else None


def registrable(domain):
    labels = domain.split(".")
    if len(labels) < 2:
        return None
    count = 3 if len(labels[-1]) == 2 and labels[-2] in COMMON_CC_SLD and len(labels) >= 3 else 2
    return ".".join(labels[-count:])


def fetch_category(category):
    request = urllib.request.Request(SOURCE.format(category=category), headers={"User-Agent": "re8ch-geodb-sync/1"})
    with urllib.request.urlopen(request, timeout=45) as response:
        source_bytes = response.read()
        text = source_bytes.decode("utf-8")
    bases = set()
    for line in text.splitlines():
        value = line.strip()
        if not value.startswith("-"):
            continue
        value = value[1:].strip().strip("'\"").lower().rstrip(".")
        match = DOMAIN_RE.fullmatch(value)
        if not match:
            continue
        base = registrable(match.group(1))
        if base:
            bases.add(base)
    return bases, hashlib.sha256(source_bytes).hexdigest()


def main():
    desired = set()
    counts = {}
    hashes = {}
    for category in CATEGORIES:
        bases, digest = fetch_category(category)
        counts[category] = len(bases)
        hashes[category] = digest
        for base in bases:
            desired.add(f"*.{base}")
    desired.update(ROOT_DOMAINS)
    if not desired or len(desired) > MAX_RESOURCES:
        raise RuntimeError(f"compiled resource count {len(desired)} outside safe range 1..{MAX_RESOURCES}")

    networks = api("GET", "/api/networks")
    network = next((item for item in networks if item["name"] == NETWORK_NAME), None)
    if not network:
        raise RuntimeError(f"network not found: {NETWORK_NAME}")
    groups = api("GET", "/api/groups")
    group = next((item for item in groups if item["name"] == RESOURCE_GROUP), None)
    if not group:
        raise RuntimeError(f"resource group not found: {RESOURCE_GROUP}")

    path = f"/api/networks/{network['id']}/resources"
    current = api("GET", path)
    managed = {item["address"]: item for item in current if item["name"].startswith("geodb:")}
    created = 0
    for address in sorted(desired):
        if address in managed:
            continue
        digest = hashlib.sha256(address.encode()).hexdigest()[:12]
        api("POST", path, {
            "name": f"geodb:{digest}",
            "description": "GeoSite compiled: " + ",".join(CATEGORIES),
            "address": address,
            "enabled": True,
            "groups": [group["id"]],
        })
        created += 1

    deleted = 0
    if PRUNE:
        for address, item in managed.items():
            if address not in desired:
                api("DELETE", f"{path}/{item['id']}")
                deleted += 1
    print(json.dumps({"categories": counts, "sha256": hashes, "desired": len(desired),
                      "created": created, "deleted": deleted, "prune": PRUNE}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"geodb-sync failed: {exc}", file=sys.stderr)
        raise
