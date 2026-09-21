#!/usr/bin/env python3
"""Retire two redundant Cloudflare records after Ontology apps cutover.

Run on R640 after the domain OCI release and explicit removal of the old
ExternalDNS Ingress. The tenant apps wildcard must already resolve to R640.
"""

import base64
import json
import subprocess
import urllib.request

BASE = "https://api.cloudflare.com/client/v4/zones/126b5f1df6cde00ac113007ddc277998/dns_records"
RECORDS = {
    "ontology.re8ch.com": ("A", "223.159.210.222"),
    "ontology.apps.re8ch.com": ("A", "223.159.210.222"),
}


def kubectl(*args):
    return subprocess.run(["sudo", "-n", "k3s", "kubectl", *args], capture_output=True, text=True)


def main():
    namespace = "re8ch-ontology-microscope"
    old = kubectl("-n", namespace, "get", "ingress", "ontology-microscope-dns-authority")
    if old.returncode == 0 or "NotFound" not in old.stderr:
        raise RuntimeError("old ExternalDNS authority remains or could not be checked")
    route_result = kubectl("-n", namespace, "get", "httproute", "ontology-microscope", "-o", "json")
    if route_result.returncode:
        raise RuntimeError("tenant app route is absent")
    route = json.loads(route_result.stdout)
    if route.get("spec", {}).get("hostnames") != ["ontology.apps.re8ch.com"]:
        raise RuntimeError("tenant app route still publishes the old hostname")
    deployment = kubectl("-n", namespace, "get", "deployment", "ontology-microscope-neuronpedia", "-o", "json")
    if deployment.returncode:
        raise RuntimeError("tenant app deployment is absent")
    env = {item["name"]: item.get("value") for item in json.loads(deployment.stdout)["spec"]["template"]["spec"]["containers"][0]["env"]}
    if any(env.get(key) != "https://ontology.apps.re8ch.com" for key in ("NEXT_PUBLIC_URL", "NEXTAUTH_URL")):
        raise RuntimeError("tenant app public URL has not switched")

    secret = kubectl("-n", "qianwen-ops", "get", "secret", "cloudflare-pg-dns-controller-secret", "-o", 'go-template={{index .data "CF_API_TOKEN"}}')
    if secret.returncode:
        raise RuntimeError("Cloudflare token is unavailable")
    token = base64.b64decode(secret.stdout.strip()).decode()
    headers = {"Authorization": "Bearer " + token}
    matched = []
    for name, expected in RECORDS.items():
        query = urllib.request.Request(BASE + "?name=" + name + "&per_page=100", headers=headers)
        with urllib.request.urlopen(query, timeout=20) as response:
            data = json.load(response)
        records = data.get("result", [])
        if not data.get("success") or len(records) != 1:
            raise RuntimeError(f"expected exactly one record for {name}")
        record = records[0]
        if (record.get("type"), record.get("content")) != expected:
            raise RuntimeError(f"unexpected record for {name}")
        matched.append(record)
    for record in matched:
        delete = urllib.request.Request(BASE + "/" + record["id"], method="DELETE", headers=headers)
        with urllib.request.urlopen(delete, timeout=20) as response:
            result = json.load(response)
        if not result.get("success"):
            raise RuntimeError(f"Cloudflare did not confirm deletion of {record['name']}")
        print(json.dumps({"deleted": record["name"], "type": record["type"], "id": record["id"]}))


if __name__ == "__main__":
    main()
