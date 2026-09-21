#!/usr/bin/env python3
"""Retire the orphaned Cloudflare cloudbeaver A record to recover quota.

Run on R640. The active CloudBeaver public route is dbgui.re8ch.com; this
legacy name has no GitOps or live Ingress/HTTPRoute owner. Token stays in memory.
"""

import base64
import json
import subprocess
import urllib.request

BASE = "https://api.cloudflare.com/client/v4/zones/126b5f1df6cde00ac113007ddc277998/dns_records"
EXPECTED = (
    "A", "cloudbeaver.re8ch.com", "1.14.174.244",
    "managed by qianwen-ops/cloudflare-panel-dns-controller",
)


def main():
    encoded = subprocess.check_output(
        ["sudo", "-n", "k3s", "kubectl", "-n", "qianwen-ops", "get", "secret",
         "cloudflare-pg-dns-controller-secret", "-o", "go-template={{index .data \"CF_API_TOKEN\"}}"],
        text=True,
    ).strip()
    token = base64.b64decode(encoded).decode()
    headers = {"Authorization": "Bearer " + token}
    request = urllib.request.Request(BASE + "?name=cloudbeaver.re8ch.com&per_page=100", headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.load(response)
    records = data.get("result", [])
    if not data.get("success") or len(records) != 1:
        raise RuntimeError("expected exactly one legacy cloudbeaver record")
    record = records[0]
    actual = tuple(record.get(key) for key in ("type", "name", "content", "comment"))
    if actual != EXPECTED:
        raise RuntimeError("legacy cloudbeaver record differs from preflight")
    delete = urllib.request.Request(BASE + "/" + record["id"], method="DELETE", headers=headers)
    with urllib.request.urlopen(delete, timeout=20) as response:
        result = json.load(response)
    if not result.get("success"):
        raise RuntimeError("Cloudflare did not confirm deletion")
    print(json.dumps({"deleted": record["name"], "type": record["type"], "content": record["content"], "id": record["id"]}))


if __name__ == "__main__":
    main()
