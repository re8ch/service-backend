#!/usr/bin/env python3
"""Copy only TLS data to netbird-access without printing decoded key bytes."""
import json, sys
source = json.load(sys.stdin)
print(json.dumps({
    "apiVersion": "v1", "kind": "Secret", "type": "kubernetes.io/tls",
    "metadata": {"name": "netbird-external-relay-tls", "namespace": "netbird-access"},
    "data": {"tls.crt": source["data"]["tls.crt"], "tls.key": source["data"]["tls.key"]},
}))
