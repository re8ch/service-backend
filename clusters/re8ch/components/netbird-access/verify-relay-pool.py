#!/usr/bin/env python3
"""Verify an N-of-M NetBird relay/STUN pool without requiring every node."""

import json
import os
import socket
import ssl
import struct
import time
import secrets


CANDIDATES = [
    {"name": "gz-1", "host": "relay-gz-1.re8ch.com", "relay": 31080, "stun": 31081},
    {"name": "hz-1", "host": "relay-hz-1.re8ch.com", "relay": 31080, "stun": 3478},
]
timeout = float(os.getenv("RELAY_VERIFY_TIMEOUT", "5"))
minimum = int(os.getenv("MIN_HEALTHY_RELAYS", "1"))
results = []

for candidate in CANDIDATES:
    item = dict(candidate)
    started = time.monotonic()
    try:
        raw = socket.create_connection((candidate["host"], candidate["relay"]), timeout)
        ctx = ssl.create_default_context()
        tls = ctx.wrap_socket(raw, server_hostname=candidate["host"])
        item["tls"] = True
        tls.close()
    except Exception as exc:
        item["tls"] = False
        item["tls_error"] = type(exc).__name__
    item["tls_connect_ms"] = round((time.monotonic() - started) * 1000, 1)

    txid = secrets.token_bytes(12)
    request = struct.pack("!HHI12s", 1, 0, 0x2112A442, txid)
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.settimeout(timeout)
    try:
        udp.sendto(request, (candidate["host"], candidate["stun"]))
        response, _ = udp.recvfrom(2048)
        item["stun"] = len(response) >= 20 and response[8:20] == txid
    except Exception as exc:
        item["stun"] = False
        item["stun_error"] = type(exc).__name__
    finally:
        udp.close()
    item["healthy"] = item["tls"] and item["stun"]
    results.append(item)

healthy = sum(item["healthy"] for item in results)
print(json.dumps({"healthy": healthy, "minimum": minimum, "candidates": results}, separators=(",", ":")))
raise SystemExit(0 if healthy >= minimum else 1)
