#!/usr/bin/env python3
"""Convert the live combined-server Secret to an external relay pool.

The Secret is read from stdin and emitted on stdout. No decoded configuration
or authentication material is logged.
"""

import base64
import json
import sys

import yaml


secret = json.load(sys.stdin)
raw = base64.b64decode(secret["data"]["config.yaml"])
config = yaml.safe_load(raw)
server = config["server"]

relay_secret = server.pop("authSecret", None)
if relay_secret is None:
    relay_secret = server.get("relays", {}).get("secret")
if not relay_secret:
    raise SystemExit("relay authentication secret is missing")
server.pop("stunPorts", None)
server["stuns"] = [
    {"uri": "stun:relay-gz-1.re8ch.com:31081", "proto": "udp"},
    {"uri": "stun:relay-hz-1.re8ch.com:3478", "proto": "udp"},
]
server["relays"] = {
    "addresses": [
        "rels://relay-gz-1.re8ch.com:31080",
        "rels://relay-hz-1.re8ch.com:31080",
    ],
    "secret": relay_secret,
    "credentialsTTL": "24h",
}

encoded = base64.b64encode(
    yaml.safe_dump(config, sort_keys=False).encode()
).decode()
secret["data"]["config.yaml"] = encoded
secret["metadata"] = {
    "name": "netbird-server-config",
    "namespace": "netbird-access",
    "annotations": {"re8ch.com/relay-pool": "gz-1,hz-1"},
}
secret.pop("status", None)
print(json.dumps(secret, separators=(",", ":")))
