#!/usr/bin/env python3
"""Retire a guarded batch of legacy apex A records after service-zone cutover.

Run on restricted-node-6dc3f140f9. Dry-run is the default; pass --apply only after the signed GitOps
change has removed every legacy ExternalDNS publication source.
"""

import argparse
import base64
import json
import subprocess
import time
import urllib.parse
import urllib.request

ZONE = "126b5f1df6cde00ac113007ddc277998"
BASE = f"https://api.cloudflare.com/client/v4/zones/{ZONE}/dns_records"
EDGE_IP = "192.0.2.138"
RECORDS = {
    "admin": EDGE_IP,
    "agent": EDGE_IP,
    "browser": EDGE_IP,
    "codex": EDGE_IP,
    "cube": EDGE_IP,
    "grafana": EDGE_IP,
    "headlamp": EDGE_IP,
    "litellm": EDGE_IP,
    "ledger-decisions": "192.0.2.45",
    "neo4j": EDGE_IP,
    "opensandbox": EDGE_IP,
    "phonycenter": EDGE_IP,
    "storage": EDGE_IP,
    "supabase": "192.0.2.192",
    "tools": EDGE_IP,
}
AUTHORITIES = ("192.0.2.62", "192.0.2.86")


def run(*argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f"command failed: {argv[0]} {argv[1]} (exit {result.returncode})")
    return result.stdout


def kubectl(*argv):
    return json.loads(run("sudo", "-n", "k3s", "kubectl", *argv, "-o", "json"))


def query_authority(host, server):
    for _ in range(3):
        output = run("dig", f"@{server}", host, "A", "+short", "+time=2", "+tries=1")
        addresses = set(output.split())
        if addresses == {EDGE_IP}:
            return
        time.sleep(1)
    raise RuntimeError(f"{server} did not consistently publish {host} to the restricted-node-6dc3f140f9 edge")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    edge = kubectl("-n", "regional-routing", "get", "publicedge", "loudi-r640")
    statuses = edge.get("status", {}).get("services", {})
    ingresses = kubectl("get", "ingresses", "-A").get("items", [])
    routes = kubectl("get", "httproutes", "-A").get("items", [])
    certificate = kubectl("-n", "kube-system", "get", "certificate", "re8ch-service-public-gateway")
    if not any(c.get("type") == "Ready" and c.get("status") == "True" for c in certificate.get("status", {}).get("conditions", [])):
        raise RuntimeError("service-zone wildcard certificate is not Ready")

    for name in RECORDS:
        host = f"{name}.service.re8ch.com"
        if statuses.get(f"{name}-service", {}).get("ready") is not True:
            raise RuntimeError(f"PublicEdge has not marked {host} Ready")
        if not any(
            host in route.get("spec", {}).get("hostnames", [])
            and any(c.get("type") == "Accepted" and c.get("status") == "True" for p in route.get("status", {}).get("parents", []) for c in p.get("conditions", []))
            and any(c.get("type") == "ResolvedRefs" and c.get("status") == "True" for p in route.get("status", {}).get("parents", []) for c in p.get("conditions", []))
            for route in routes
        ):
            raise RuntimeError(f"no accepted/resolved HTTPRoute for {host}")
        old = f"{name}.re8ch.com"
        for ingress in ingresses:
            if ingress.get("metadata", {}).get("labels", {}).get("networking.re8ch.com/dns-authority") != "public-edge":
                continue
            annotation = ingress.get("metadata", {}).get("annotations", {}).get("external-dns.alpha.kubernetes.io/hostname", "")
            hosts = [x.strip() for x in annotation.split(",")]
            hosts += [rule.get("host") for rule in ingress.get("spec", {}).get("rules", [])]
            if old in hosts:
                raise RuntimeError(f"legacy ExternalDNS writer remains for {old}")
        for server in AUTHORITIES:
            query_authority(host, server)
            time.sleep(0.3)

    secret = kubectl("-n", "qianwen-ops", "get", "secret", "cloudflare-pg-dns-controller-secret")
    token = base64.b64decode(secret["data"]["CF_API_TOKEN"]).decode()
    headers = {"Authorization": "Bearer " + token}
    found = []
    for name, expected in RECORDS.items():
        host = f"{name}.re8ch.com"
        query = urllib.request.Request(BASE + "?" + urllib.parse.urlencode({"name": host, "type": "A", "per_page": 100}), headers=headers)
        with urllib.request.urlopen(query, timeout=20) as response:
            data = json.load(response)
        if not data.get("success"):
            raise RuntimeError(f"Cloudflare lookup failed for {host}")
        records = data.get("result", [])
        if len(records) > 1 or (records and (records[0].get("name"), records[0].get("type"), records[0].get("content")) != (host, "A", expected)):
            raise RuntimeError(f"Cloudflare record changed unexpectedly: {host}")
        if records:
            found.append(records[0])
    print(json.dumps({"preflight": "passed", "candidates": len(RECORDS), "records_to_delete": len(found), "apply": args.apply}))
    if not args.apply:
        return
    for record in found:
        request = urllib.request.Request(BASE + "/" + record["id"], method="DELETE", headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)
        if not result.get("success"):
            raise RuntimeError(f"Cloudflare deletion failed: {record['name']}")
        print(json.dumps({"deleted": record["name"], "type": "A"}))


if __name__ == "__main__":
    main()
