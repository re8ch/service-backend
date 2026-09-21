#!/usr/bin/env python3
"""Reclaim obsolete imported apex NS records and delegate service.re8ch.com.

Run on R640, where the existing Cloudflare token Secret is available. No token
is passed as an argument or printed. Default mode is read-only; --apply writes.
"""

import argparse
import base64
import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request

ZONE = "re8ch.com"
CHILD = "service.re8ch.com"
AUTHORITIES = ("ns-sh.re8ch.com", "ns-gz.re8ch.com", "ns-us.re8ch.com")
LEGACY_APEX_NS = frozenset(f"ns-cloud-a{i}.googledomains.com" for i in range(1, 5))
API = "https://api.cloudflare.com/client/v4"


def token_from_cluster():
    encoded = subprocess.check_output(
        ["sudo", "-n", "k3s", "kubectl", "-n", "qianwen-ops", "get", "secret",
         "cloudflare-pg-dns-controller-secret", "-o", "go-template={{index .data \"CF_API_TOKEN\"}}"],
        text=True,
    ).strip()
    return base64.b64decode(encoded).decode()


def request(token, method, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        API + path, data=body, method=method,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Cloudflare {method} failed: HTTP {exc.code}: {exc.read().decode()}") from exc
    if not data.get("success"):
        raise RuntimeError(f"Cloudflare {method} failed: {data.get('errors')}")
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    token = token_from_cluster()
    zones = request(token, "GET", "/zones?name=re8ch.com&per_page=1")["result"]
    if len(zones) != 1 or zones[0]["name"] != ZONE or zones[0]["status"] != "active":
        raise RuntimeError("expected one active re8ch.com zone")
    zone = zones[0]["id"]

    def records(name):
        path = f"/zones/{zone}/dns_records?name={urllib.parse.quote(name)}&per_page=100"
        return request(token, "GET", path)["result"]

    apex_ns = [r for r in records(ZONE) if r["type"] == "NS"]
    legacy = [r for r in apex_ns if r["content"].rstrip(".") in LEGACY_APEX_NS]
    unexpected_apex_ns = [r for r in apex_ns if r not in legacy]
    if unexpected_apex_ns:
        raise RuntimeError("unexpected apex NS record; refusing to alter delegation")
    child = records(CHILD)
    if any(r["type"] != "NS" or r["content"].rstrip(".") not in AUTHORITIES for r in child):
        raise RuntimeError("unexpected existing service-zone record; refusing to replace it")
    for authority in AUTHORITIES:
        glue = [r for r in records(authority) if r["type"] == "A"]
        if not glue:
            raise RuntimeError(f"missing parent-zone A record for {authority}")

    print(json.dumps({"apply": args.apply, "delete_obsolete_apex_ns": sorted(r["content"] for r in legacy),
                      "existing_child_ns": sorted(r["content"] for r in child),
                      "create_child_ns": sorted(set(AUTHORITIES) - {r["content"].rstrip(".") for r in child})}))
    if not args.apply:
        return

    # Deleting only exact imported records leaves the registrar's Cloudflare NS
    # assignment unchanged. Re-running after partial failure is intentionally safe.
    for record in legacy:
        request(token, "DELETE", f"/zones/{zone}/dns_records/{record['id']}")
    existing = {r["content"].rstrip(".") for r in records(CHILD) if r["type"] == "NS"}
    for authority in AUTHORITIES:
        if authority not in existing:
            request(token, "POST", f"/zones/{zone}/dns_records",
                    {"type": "NS", "name": CHILD, "content": authority,
                     "ttl": 300, "comment": "PublicEdge service-zone delegation"})
    final = {r["content"].rstrip(".") for r in records(CHILD) if r["type"] == "NS"}
    if final != set(AUTHORITIES):
        raise RuntimeError(f"incomplete delegation: {sorted(final)}")
    print(json.dumps({"delegated": CHILD, "nameservers": sorted(final)}))


if __name__ == "__main__":
    main()
