#!/usr/bin/env python3
"""Elect reachable public DNS authorities and reconcile runtime and parent DNS.

Runs inside regional-routing with read access to Nodes, PublicEdges and the
Cloudflare token, and write access only to its election ConfigMap.
"""

import base64
import concurrent.futures
import ipaddress
import json
import os
import socket
import ssl
import struct
import time
import urllib.parse
import urllib.error
import urllib.request


API = "https://api.cloudflare.com/client/v4"
GROUP = "networking.re8ch.com"
ZONE = "re8ch.com"
CHILD_ZONES = ("auth", "api", "db-ro", "db-rw", "service", "functions")
ELECTION_NAME = "public-edge-nameservers"
NS_ANNOTATION = f"{GROUP}/dns-nameserver"
CAPACITY_ANNOTATION = f"{GROUP}/dns-capacity-mbps"
REGION_LABEL = "topology.kubernetes.io/region"
REGION_ANNOTATION = f"{GROUP}/dns-region"
SA = "/var/run/secrets/kubernetes.io/serviceaccount"


def api_request(path, method="GET", payload=None):
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
    with open(f"{SA}/token", encoding="utf-8") as stream:
        token = stream.read().strip()
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        f"https://{host}:{port}{path}", data=body, method=method,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/merge-patch+json" if method == "PATCH" else "application/json"},
    )
    context = ssl.create_default_context(cafile=f"{SA}/ca.crt")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, context=context, timeout=10) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
            time.sleep(2 ** attempt)


def encode_name(name):
    output = bytearray()
    for label in name.rstrip(".").split("."):
        encoded = label.encode("ascii")
        if not 0 < len(encoded) < 64:
            raise ValueError("invalid DNS label")
        output.append(len(encoded))
        output.extend(encoded)
    return bytes(output) + b"\0"


def parse_name(packet, offset):
    labels = []
    resume = None
    for _ in range(128):
        length = packet[offset]
        offset += 1
        if length == 0:
            return ".".join(labels).lower() + ".", resume or offset
        if length & 0xC0 == 0xC0:
            pointer = ((length & 0x3F) << 8) | packet[offset]
            offset += 1
            if resume is None:
                resume = offset
            offset = pointer
            continue
        if length > 63:
            raise ValueError("invalid DNS label")
        labels.append(packet[offset:offset + length].decode("ascii"))
        offset += length
    raise ValueError("DNS compression loop")


def dns_query(ip, name, qtype, tcp=False):
    ident = os.urandom(2)
    query = ident + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + encode_name(name) + struct.pack("!HH", qtype, 1)
    if tcp:
        with socket.create_connection((ip, 53), timeout=2) as sock:
            sock.settimeout(2)
            sock.sendall(struct.pack("!H", len(query)) + query)
            size = sock.recv(2)
            if len(size) != 2:
                raise OSError("short TCP DNS length")
            length = struct.unpack("!H", size)[0]
            result = bytearray()
            while len(result) < length:
                part = sock.recv(length - len(result))
                if not part:
                    raise OSError("short TCP DNS response")
                result.extend(part)
            packet = bytes(result)
    else:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(2)
            sock.sendto(query, (ip, 53))
            packet, peer = sock.recvfrom(4096)
            if peer[0] != ip:
                raise ValueError("DNS response from unexpected address")
    if len(packet) < 12 or packet[:2] != ident:
        raise ValueError("invalid DNS transaction")
    _, flags, questions, answers, _, _ = struct.unpack("!HHHHHH", packet[:12])
    if flags & 0x8000 == 0 or flags & 0x0400 == 0 or flags & 0x000F or questions != 1 or answers < 1:
        raise ValueError("not an authoritative positive DNS answer")
    offset = 12
    _, offset = parse_name(packet, offset)
    offset += 4
    values = []
    for _ in range(answers):
        _, offset = parse_name(packet, offset)
        rrtype, _, _, length = struct.unpack("!HHIH", packet[offset:offset + 10])
        offset += 10
        if rrtype == qtype and qtype == 2:
            value, _ = parse_name(packet, offset)
            values.append(value)
        elif rrtype == qtype:
            values.append("ok")
        offset += length
    if not values:
        raise ValueError("expected answer type is absent")
    return values


def probe(candidate):
    ip = candidate["ip"]
    started = time.monotonic()
    try:
        for round_number in range(2):
            if round_number:
                time.sleep(1)
            for zone in CHILD_ZONES:
                name = f"{zone}.{ZONE}."
                for tcp in (False, True):
                    dns_query(ip, name, 6, tcp=tcp)
        candidate["latencyMs"] = int((time.monotonic() - started) * 1000)
        return candidate
    except (OSError, ValueError, IndexError, UnicodeError) as exc:
        print(f"probe rejected node={candidate['node']} ip={ip} reason={exc}", flush=True)
        return None


def discover(nodes, edges):
    by_node = {item.get("spec", {}).get("nodeName"): item.get("spec", {})
               for item in edges.get("items", [])}
    candidates = []
    for item in nodes.get("items", []):
        metadata = item["metadata"]
        if metadata.get("deletionTimestamp"):
            continue
        addresses = [entry["address"] for entry in item.get("status", {}).get("addresses", [])
                     if entry.get("type") == "ExternalIP"]
        spec = by_node.get(metadata["name"], {})
        annotations = metadata.get("annotations", {})
        labels = metadata.get("labels", {})
        for ip in addresses:
            try:
                if not ipaddress.ip_address(ip).is_global or ":" in ip:
                    continue
                capacity = max(1, int(annotations.get(CAPACITY_ANNOTATION, spec.get("capacityMbps", 1))))
            except ValueError:
                continue
            ns = annotations.get(NS_ANNOTATION, f"ns-{metadata['name']}.{ZONE}.").rstrip(".").lower() + "."
            if not ns.endswith(f".{ZONE}."):
                raise ValueError(f"nameserver outside parent zone: {ns}")
            encode_name(ns)
            candidates.append({"node": metadata["name"], "ip": ip, "ns": ns,
                               "capacityMbps": capacity,
                               "region": labels.get(REGION_LABEL, annotations.get(REGION_ANNOTATION,
                                                                                   spec.get("region", "unknown")))})
    return candidates


def elect(candidates, limit=3):
    selected = []
    remaining = list(candidates)
    while remaining and len(selected) < limit:
        regions = {item["region"] for item in selected}
        chosen = max(remaining, key=lambda item: (
            item["region"] not in regions, item["capacityMbps"], -item.get("latencyMs", 0),
            item["node"],
        ))
        selected.append(chosen)
        remaining.remove(chosen)
    if len({item["ns"] for item in selected}) != len(selected):
        raise ValueError("elected nameservers are not unique")
    return selected


def cloudflare(token, method, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(API + path, data=body, method=method,
                                     headers={"Authorization": f"Bearer {token}",
                                              "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.load(response)
    if not result.get("success"):
        raise RuntimeError(f"Cloudflare {method} {path}: {result.get('errors')}")
    return result["result"]


def reconcile_parent(token, selected):
    zones = cloudflare(token, "GET", f"/zones?name={ZONE}&per_page=1")
    if len(zones) != 1 or zones[0].get("status") != "active":
        raise RuntimeError("parent zone is not uniquely active")
    prefix = f"/zones/{zones[0]['id']}/dns_records"

    def records(name):
        return cloudflare(token, "GET", f"{prefix}?name={urllib.parse.quote(name)}&per_page=100")

    # Add or correct glue before changing any delegation.
    for item in selected:
        name = item["ns"].rstrip(".")
        current = records(name)
        if any(record["type"] != "A" for record in current):
            raise RuntimeError(f"conflicting glue record at {name}")
        if not any(record["content"] == item["ip"] for record in current):
            cloudflare(token, "POST", prefix, {"type": "A", "name": name, "content": item["ip"],
                                                "ttl": 60, "proxied": False,
                                                "comment": "PublicEdge elected authority"})
        for record in current:
            if record["content"] != item["ip"]:
                cloudflare(token, "DELETE", f"{prefix}/{record['id']}")

    desired = {item["ns"].rstrip(".") for item in selected}
    for child in CHILD_ZONES:
        name = f"{child}.{ZONE}"
        current = [record for record in records(name) if record["type"] == "NS"]
        existing = {record["content"].rstrip(".") for record in current}
        for ns in sorted(desired - existing):
            cloudflare(token, "POST", prefix, {"type": "NS", "name": name, "content": ns,
                                                "ttl": 300, "comment": "PublicEdge elected delegation"})
        for record in current:
            if record["content"].rstrip(".") not in desired:
                cloudflare(token, "DELETE", f"{prefix}/{record['id']}")
        final = {record["content"].rstrip(".") for record in records(name) if record["type"] == "NS"}
        if final != desired:
            raise RuntimeError(f"delegation mismatch at {name}: {sorted(final)}")


def main():
    nodes = api_request("/api/v1/nodes")
    try:
        edges = api_request(f"/apis/{GROUP}/v1alpha1/publicedges")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        # PublicEdges only enrich capacity and region. ExternalIP plus the
        # authoritative public DNS probe remain the election requirements.
        print(f"PublicEdge metadata unavailable; using Node facts: {exc}", flush=True)
        edges = {"items": []}
    discovered = discover(nodes, edges)
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(12, len(discovered) or 1)) as pool:
        qualified = [item for item in pool.map(probe, discovered) if item]
    selected = elect(qualified)
    if not selected:
        raise RuntimeError("no externally reachable authority; preserving last election")
    nameservers = [item["ns"] for item in selected]
    print(json.dumps({"discovered": len(discovered), "qualified": len(qualified),
                      "selected": selected}), flush=True)
    payload = {"metadata": {"name": ELECTION_NAME, "namespace": "regional-routing"},
               "data": {"nameservers.json": json.dumps({"nameservers": nameservers}, separators=(",", ":"))}}
    path = f"/api/v1/namespaces/regional-routing/configmaps/{ELECTION_NAME}"
    try:
        api_request(path, "PATCH", payload)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        api_request("/api/v1/namespaces/regional-routing/configmaps", "POST",
                    {"apiVersion": "v1", "kind": "ConfigMap", **payload})
    # Wait for kubelet projection and authority reload before parent delegation.
    for _ in range(20):
        try:
            desired = set(nameservers)
            if all(set(dns_query(item["ip"], f"{zone}.{ZONE}.", 2)) == desired
                   for item in selected for zone in CHILD_ZONES):
                break
        except (OSError, ValueError, IndexError, UnicodeError):
            pass
        time.sleep(3)
    else:
        raise RuntimeError("elected authorities did not load the same NS set")
    secret = api_request("/api/v1/namespaces/qianwen-ops/secrets/cloudflare-pg-dns-controller-secret")
    token = base64.b64decode(secret["data"]["CF_API_TOKEN"]).decode()
    reconcile_parent(token, selected)


if __name__ == "__main__":
    main()
