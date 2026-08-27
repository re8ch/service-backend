#!/usr/bin/env python3
import http.client
import ipaddress
import json
import os
import socket
import socketserver
import ssl
import struct
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NODE = os.getenv("NODE_NAME", "unknown")
PROBE_CONNECT_TIMEOUT = float(os.getenv("PROBE_CONNECT_TIMEOUT_SECONDS", "5"))
PROBE_RESPONSE_TIMEOUT = float(os.getenv("PROBE_RESPONSE_TIMEOUT_SECONDS", "12"))
PROBE_INTERVAL_SECONDS = max(1, int(os.getenv("PROBE_INTERVAL_SECONDS", "3")))
NAMESERVERS = os.getenv("NAMESERVERS", "ns-sh.re8ch.com. ns-gz.re8ch.com. ns-us.re8ch.com.").split()
CANDIDATES = json.loads(os.getenv("CANDIDATES_JSON", "[]"))
SERVICE_DEFINITIONS = json.loads(os.getenv("SERVICES_JSON", '''{
  "api.re8ch.com.": {"service": "api", "paths": ["auth", "nuclio", "ledger", "image", "compocv"]},
  "db-ro.re8ch.com.": {"service": "db-ro", "paths": ["postgrest"]},
  "db-rw.re8ch.com.": {"service": "db-rw", "paths": ["postgrest"]}
}'''))
SERVICES = {name: definition["service"] for name, definition in SERVICE_DEFINITIONS.items()}
AUTHORITY_REGIONS = json.loads(os.getenv("AUTHORITY_REGIONS_JSON", "{}"))
AUTHORITY_AREAS = json.loads(os.getenv("AUTHORITY_AREAS_JSON", "{}"))
REGION = AUTHORITY_REGIONS.get(NODE, next((item["region"] for item in CANDIDATES if item["id"] == NODE), "unknown"))
AREA = AUTHORITY_AREAS.get(NODE, next((item.get("area", item["region"]) for item in CANDIDATES if item["id"] == NODE), REGION))
LOCK = threading.Lock()
HEALTH = {service: {} for service in SERVICES.values()}
KUBERNETES_API = os.getenv("KUBERNETES_SERVICE_HOST", "")
SERVICE_ACCOUNT = "/var/run/secrets/kubernetes.io/serviceaccount"
PUBLISHER_NODE = os.getenv("PUBLISHER_NODE", "")
PUBLICATION_REFS = json.loads(os.getenv("PUBLICATION_REFS_JSON", "{}"))
CANDIDATE_CAPACITY_WEIGHT = max(0, int(os.getenv("CANDIDATE_CAPACITY_WEIGHT", "10")))
CANDIDATE_LOCAL_AREA_BONUS = max(0, int(os.getenv("CANDIDATE_LOCAL_AREA_BONUS", "100000")))
CANDIDATE_PRIORITY_WEIGHT = max(0, int(os.getenv("CANDIDATE_PRIORITY_WEIGHT", "1")))
CANDIDATE_LATENCY_DIVISOR_MS = max(1, int(os.getenv("CANDIDATE_LATENCY_DIVISOR_MS", "20")))
CANDIDATE_LATENCY_PENALTY_CAP = max(0, int(os.getenv("CANDIDATE_LATENCY_PENALTY_CAP", "50")))
CANDIDATE_LOCAL_NODE_BONUS = max(0, int(os.getenv("CANDIDATE_LOCAL_NODE_BONUS", "5")))


def patroni_write_authority_ready():
    authority = kubernetes_get("/api/v1/namespaces/kube-system/configmaps/pg-re8ch-role-authority")
    endpoint_slice = kubernetes_get("/apis/discovery.k8s.io/v1/namespaces/pigsty-external/endpointslices/pg-re8ch-primary")
    try:
        result = json.loads(authority["data"]["authority.json"])
        addresses = [address for endpoint in endpoint_slice.get("endpoints", [])
                     if endpoint.get("conditions", {}).get("ready", True)
                     for address in endpoint.get("addresses", [])]
        return result.get("authority") == "patroni" and result["leader"]["address"] in addresses and len(addresses) == 1
    except (KeyError, TypeError, ValueError):
        return False


def kubernetes_get(path):
    if not KUBERNETES_API:
        return None
    try:
        with open(f"{SERVICE_ACCOUNT}/token", encoding="utf-8") as stream:
            token = stream.read().strip()
        context = ssl.create_default_context(cafile=f"{SERVICE_ACCOUNT}/ca.crt")
        request = urllib.request.Request(
            f"https://{KUBERNETES_API}:{os.getenv('KUBERNETES_SERVICE_PORT_HTTPS', '443')}{path}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(request, context=context, timeout=3) as response:
            return json.load(response)
    except Exception as exc:
        print(f"kubernetes_get path={path} error={exc}", flush=True)
        return None


def kubernetes_patch(path, payload):
    with open(f"{SERVICE_ACCOUNT}/token", encoding="utf-8") as stream:
        token = stream.read().strip()
    context = ssl.create_default_context(cafile=f"{SERVICE_ACCOUNT}/ca.crt")
    request = urllib.request.Request(
        f"https://{KUBERNETES_API}:{os.getenv('KUBERNETES_SERVICE_PORT_HTTPS', '443')}{path}",
        data=json.dumps(payload).encode(), method="PATCH",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/merge-patch+json"},
    )
    with urllib.request.urlopen(request, context=context, timeout=5) as response:
        return json.load(response)


def candidates_from_public_edges():
    payload = kubernetes_get("/apis/networking.re8ch.com/v1alpha1/publicedges")
    if not payload:
        return []
    candidates = []
    for item in payload.get("items", []):
        spec = item.get("spec", {})
        endpoint = spec.get("endpoint", {})
        if not spec.get("enabled") or spec.get("draining"):
            continue
        service_classes = set(spec.get("serviceClasses", []))
        probes = {}
        for hostname, definition in SERVICE_DEFINITIONS.items():
            service = definition["service"]
            service_class = definition.get("class", "web")
            if service_class not in service_classes:
                continue
            probes[service] = f"https://{hostname.rstrip('.')}{definition.get('probePath', '/')}"
        candidates.append({
            "id": item["metadata"]["name"],
            "nodeName": spec.get("nodeName", ""),
            "region": spec["region"],
            "area": spec.get("area", spec["region"]),
            "ip": endpoint["value"],
            "probeIp": endpoint.get("probeAddress", endpoint["value"]),
            "endpointType": endpoint["type"],
            "gatewayVip": spec["gatewayVIP"],
            "priority": spec.get("priority", 0),
            "capacityMbps": spec.get("capacityMbps", 1),
            "priorityByRegion": spec.get("priorityByRegion", {}),
            "forwarding": spec.get("forwarding", {"mode": "DirectGateway"}),
            "probes": probes,
        })
    return candidates


def refresh_candidates():
    discovered = candidates_from_public_edges()
    if discovered:
        with LOCK:
            CANDIDATES[:] = discovered
            for service in SERVICES.values():
                HEALTH.setdefault(service, {})


def bind_address():
    per_node = json.loads(os.getenv("BIND_ADDRESSES_JSON", "{}"))
    if NODE in per_node:
        return per_node[NODE]
    configured = os.getenv("BIND_ADDRESS", "").strip()
    if configured:
        return configured
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("1.1.1.1", 53))
        return sock.getsockname()[0]
    finally:
        sock.close()


def accepted_probe_status(service, status):
    definition = next(
        (item for item in SERVICE_DEFINITIONS.values() if item["service"] == service),
        {},
    )
    accepted_statuses = definition.get("acceptedStatuses")
    if accepted_statuses is not None:
        return status in accepted_statuses
    return 200 <= status < 400 or status in (401, 403)


def probe(candidate, service, url):
    parsed = urllib.parse.urlparse(url)
    started = time.monotonic()
    status = 0
    failure = ""
    try:
        context = ssl.create_default_context()
        probe_ip = candidate.get("probeIp", candidate.get("localIp", candidate["ip"]))
        sock = socket.create_connection((probe_ip, 443), timeout=PROBE_CONNECT_TIMEOUT)
        tls = context.wrap_socket(sock, server_hostname=parsed.hostname)
        connection = http.client.HTTPConnection(parsed.hostname, timeout=PROBE_RESPONSE_TIMEOUT)
        connection.sock = tls
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        connection.request("GET", path, headers={"Host": parsed.hostname, "User-Agent": "re8ch-routing-health/1"})
        response = connection.getresponse()
        status = response.status
        response.read(4096)
        connection.close()
    except Exception as exc:
        failure = str(exc)
    latency = int((time.monotonic() - started) * 1000)
    with LOCK:
        previous = HEALTH[service].get(candidate["id"], {})
        if accepted_probe_status(service, status):
            successes = previous.get("successes", 0) + 1
            failures = 0
            ready = previous.get("ready", False) or successes >= 2
        else:
            successes = 0
            failures = previous.get("failures", 0) + 1
            ready = previous.get("ready", False) and failures < 2
        HEALTH[service][candidate["id"]] = {
            "ready": ready,
            "statusCode": status,
            "latencyMs": latency,
            "successes": successes,
            "failures": failures,
            "observedAt": int(time.time()),
            "failure": failure,
        }


def probe_all():
    refresh_candidates()
    threads = []
    for candidate in CANDIDATES:
        for service, url in candidate.get("probes", {}).items():
            thread = threading.Thread(target=probe, args=(candidate, service, url), daemon=True)
            thread.start()
            threads.append(thread)
    for thread in threads:
        thread.join(timeout=PROBE_CONNECT_TIMEOUT + PROBE_RESPONSE_TIMEOUT + 2)


def probe_loop():
    while True:
        probe_all()
        publish_default_area()
        time.sleep(PROBE_INTERVAL_SECONDS)


def publish_default_area():
    """Update the one ExternalDNS authority object for non-delegated names.

    Regional NS answers stay request-area aware. Cloudflare A records cannot,
    so one elected authority publishes the configured default area (CN) and
    removes DNS ownership from application Routes.
    """
    if NODE != PUBLISHER_NODE:
        return
    for service, ref in PUBLICATION_REFS.items():
        try:
            selected = next((item for item in ranked(service) if item["state"] == "ready"), None)
            if not selected:
                continue
            path = f"/apis/networking.k8s.io/v1/namespaces/{ref['namespace']}/ingresses/{ref['name']}"
            current = kubernetes_get(path)
            annotations = (current or {}).get("metadata", {}).get("annotations", {})
            if annotations.get("external-dns.alpha.kubernetes.io/target") == selected["ip"]:
                continue
            kubernetes_patch(path, {"metadata": {"annotations": {
                "external-dns.alpha.kubernetes.io/target": selected["ip"],
                "networking.re8ch.com/selected-public-edge": selected["id"],
            }}})
        except Exception as exc:
            print(f"publication service={service} error={exc}", flush=True)


def ranked(service):
    database_authority_ready = service != "db-rw" or patroni_write_authority_ready()
    with LOCK:
        snapshot = dict(HEALTH.get(service, {}))
    result = []
    with LOCK:
        candidates = list(CANDIDATES)
    for candidate in candidates:
        if service not in candidate.get("probes", {}):
            continue
        observed = snapshot.get(candidate["id"], {})
        ready = bool(observed.get("ready")) and database_authority_ready
        score = 0
        if ready:
            regional_priority = candidate.get("priorityByRegion", {}).get(REGION, candidate.get("priority", 0))
            # Area is the hard locality boundary: a US authority should publish a
            # healthy US relay even when the origin is in CN. Inside one area,
            # capacity is intentionally the dominant signal so CN clients reach
            # restricted-node-6dc3f140f9 directly instead of hairpinning through a 100 Mbps edge.
            score = int(candidate.get("capacityMbps", 1)) * CANDIDATE_CAPACITY_WEIGHT
            if candidate.get("area", candidate["region"]) == AREA:
                score += CANDIDATE_LOCAL_AREA_BONUS
            score -= int(regional_priority) * CANDIDATE_PRIORITY_WEIGHT
            score -= min(int(observed.get("latencyMs", 0)) // CANDIDATE_LATENCY_DIVISOR_MS, CANDIDATE_LATENCY_PENALTY_CAP)
            if candidate["id"] == NODE:
                score += CANDIDATE_LOCAL_NODE_BONUS
        result.append({
            "id": candidate["id"], "region": candidate["region"],
            "area": candidate.get("area", candidate["region"]), "ip": candidate["ip"],
            "capacityMbps": candidate.get("capacityMbps", 1),
            "endpointType": candidate.get("endpointType", "PublicIP"),
            "gatewayVip": candidate.get("gatewayVip", ""),
            "forwarding": candidate.get("forwarding", {"mode": "DirectGateway"}),
            "paths": candidate.get("paths", {}).get(service, []),
            "score": score, "state": "ready" if ready else "unavailable",
            "statusCode": observed.get("statusCode", 0), "latencyMs": observed.get("latencyMs", 0),
            "observedAt": observed.get("observedAt", 0),
            "reason": (observed.get("failure", "") if database_authority_ready else "Patroni role authority unavailable"),
        })
    return sorted(result, key=lambda item: (-item["score"], item["id"]))


def encode_name(name):
    output = bytearray()
    for label in name.rstrip(".").split("."):
        encoded = label.encode("ascii")
        output.append(len(encoded))
        output.extend(encoded)
    output.append(0)
    return bytes(output)


def parse_name(packet, offset):
    labels = []
    while offset < len(packet):
        size = packet[offset]
        offset += 1
        if size == 0:
            return ".".join(labels).lower() + ".", offset
        if size > 63 or offset + size > len(packet):
            raise ValueError("invalid DNS name")
        labels.append(packet[offset:offset + size].decode("ascii"))
        offset += size
    raise ValueError("unterminated DNS name")


def rr(name, qtype, ttl, data):
    return encode_name(name) + struct.pack("!HHIH", qtype, 1, ttl, len(data)) + data


def dns_response(query):
    if len(query) < 12:
        return b""
    ident, flags, qdcount = struct.unpack("!HHH", query[:6])
    if qdcount != 1:
        return b""
    try:
        name, offset = parse_name(query, 12)
    except ValueError:
        return b""
    if offset + 4 > len(query):
        return b""
    qtype, _ = struct.unpack("!HH", query[offset:offset + 4])
    question = query[12:offset + 4]
    answers = []
    rcode = 0
    service = SERVICES.get(name)
    if not service:
        rcode = 3
    elif qtype in (1, 255):
        ready = [item for item in ranked(service) if item["state"] == "ready"]
        if ready:
            best_score = ready[0]["score"]
            for selected in [item for item in ready if item["score"] == best_score]:
                if selected.get("endpointType") == "HostnameTunnel":
                    answers.append(rr(name, 5, 30, encode_name(selected["ip"])))
                else:
                    address = ipaddress.ip_address(selected["ip"])
                    if address.version == 4:
                        answers.append(rr(name, 1, 30, address.packed))
    elif qtype == 2:
        answers.extend(rr(name, 2, 300, encode_name(ns)) for ns in NAMESERVERS)
    elif qtype == 6:
        serial = int(time.strftime("%Y%m%d") + "01")
        data = encode_name(NAMESERVERS[0]) + encode_name("hostmaster.re8ch.com.") + struct.pack("!IIIII", serial, 60, 60, 86400, 30)
        answers.append(rr(name, 6, 300, data))
    response_flags = 0x8400 | (flags & 0x0100) | rcode
    header = struct.pack("!HHHHHH", ident, response_flags, 1, len(answers), 0, 0)
    return header + question + b"".join(answers)


class UDPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        response = dns_response(data)
        if response:
            sock.sendto(response, self.client_address)


class ReusableUDPServer(socketserver.ThreadingUDPServer):
    allow_reuse_address = True


class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


class TCPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        length_data = self.request.recv(2)
        if len(length_data) != 2:
            return
        length = struct.unpack("!H", length_data)[0]
        data = b""
        while len(data) < length:
            part = self.request.recv(length - len(data))
            if not part:
                return
            data += part
        response = dns_response(data)
        if response:
            self.request.sendall(struct.pack("!H", len(response)) + response)


class HTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok\n")
            return
        if parsed.path != "/v1/discovery":
            self.send_error(404)
            return
        query = urllib.parse.parse_qs(parsed.query)
        service = query.get("service", [""])[0]
        if service == "db":
            service = "db-" + query.get("mode", [""])[0]
        if service not in set(SERVICES.values()):
            self.send_error(400, "unknown service")
            return
        candidates = ranked(service)
        selected = next((item["id"] for item in candidates if item["state"] == "ready"), "")
        payload = json.dumps({
            "version": 1, "generation": int(time.time() // 10 * 10), "service": service,
            "paths": next((item.get("paths", []) for item in SERVICE_DEFINITIONS.values() if item["service"] == service), []),
            "selected": selected, "ttlSeconds": 30, "servedBy": NODE,
            "client": self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip(),
            "candidates": candidates,
        }, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "public, max-age=20, stale-while-revalidate=60")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        print("http", self.address_string(), fmt % args, flush=True)


def main():
    if not CANDIDATES and not KUBERNETES_API:
        raise RuntimeError("no PublicEdge API or CANDIDATES_JSON configured")
    probe_all()
    publish_default_area()
    threading.Thread(target=probe_loop, daemon=True).start()
    dns_bind = bind_address()
    udp = ReusableUDPServer((dns_bind, 53), UDPHandler)
    tcp = ReusableTCPServer((dns_bind, 53), TCPHandler)
    threading.Thread(target=udp.serve_forever, daemon=True).start()
    threading.Thread(target=tcp.serve_forever, daemon=True).start()
    print(f"node={NODE} area={AREA} region={REGION} dns={dns_bind}:53 http=:8080", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), HTTPHandler).serve_forever()


if __name__ == "__main__":
    main()
