#!/usr/bin/env python3
"""Retire Farseer's old Cloudflare CNAME after its tenant-app cutover.

Run on restricted-node-6dc3f140f9 only after the GitOps removal of the old ExternalDNS Ingress and
HTTPRoute has reconciled. The Cloudflare token remains in this process memory.
"""

import base64
import json
import subprocess
import urllib.request

BASE = "https://api.cloudflare.com/client/v4/zones/126b5f1df6cde00ac113007ddc277998/dns_records"
NAME = "farseer.re8ch.com"
TARGET = "farseer.re8ch.com.eo.dnse1.com"


def kubectl(*args):
    return subprocess.run(
        ["sudo", "-n", "k3s", "kubectl", *args],
        capture_output=True, text=True, check=False,
    )


def main():
    for kind, name in (("ingress", "farseer-public-dns"), ("httproute", "farseer-web")):
        result = kubectl("-n", "farseer", "get", kind, name)
        if result.returncode == 0 or "NotFound" not in result.stderr:
            raise RuntimeError(f"old {kind}/{name} remains or could not be checked")

    new_route = kubectl("-n", "farseer", "get", "httproute", "farseer-tenant-app-public", "-o", "json")
    if new_route.returncode:
        raise RuntimeError("new tenant-app HTTPRoute is absent")
    route = json.loads(new_route.stdout)
    if "farseer.apps.re8ch.com" not in route.get("spec", {}).get("hostnames", []):
        raise RuntimeError("new tenant-app HTTPRoute has unexpected hostname")
    conditions = {
        condition["type"]: condition.get("status")
        for parent in route.get("status", {}).get("parents", [])
        for condition in parent.get("conditions", [])
    }
    if conditions.get("Accepted") != "True" or conditions.get("ResolvedRefs") != "True":
        raise RuntimeError("new tenant-app HTTPRoute is not accepted and resolved")

    secret = kubectl(
        "-n", "qianwen-ops", "get", "secret", "cloudflare-pg-dns-controller-secret",
        "-o", 'go-template={{index .data "CF_API_TOKEN"}}',
    )
    if secret.returncode:
        raise RuntimeError("Cloudflare token is unavailable")
    token = base64.b64decode(secret.stdout.strip()).decode()
    headers = {"Authorization": "Bearer " + token}
    query = urllib.request.Request(BASE + "?name=" + NAME + "&per_page=100", headers=headers)
    with urllib.request.urlopen(query, timeout=20) as response:
        data = json.load(response)
    records = data.get("result", [])
    if not data.get("success") or len(records) != 1:
        raise RuntimeError("expected exactly one old Farseer DNS record")
    record = records[0]
    if (record.get("type"), record.get("name"), record.get("content")) != ("CNAME", NAME, TARGET):
        raise RuntimeError("old Farseer DNS record differs from preflight")
    delete = urllib.request.Request(BASE + "/" + record["id"], method="DELETE", headers=headers)
    with urllib.request.urlopen(delete, timeout=20) as response:
        result = json.load(response)
    if not result.get("success"):
        raise RuntimeError("Cloudflare did not confirm deletion")
    print(json.dumps({"deleted": NAME, "type": "CNAME", "target": TARGET, "id": record["id"]}))


if __name__ == "__main__":
    main()
