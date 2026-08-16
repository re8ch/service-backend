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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NODE = os.getenv("NODE_NAME", "unknown")
NAMESERVERS = os.getenv("NAMESERVERS", "ns-sh.re8ch.com. ns-gz.re8ch.com. ns-us.re8ch.com.").split()
CANDIDATES = json.loads(os.environ["CANDIDATES_JSON"])
SERVICES = {"api.re8ch.com.": "api", "db-ro.re8ch.com.": "db-ro", "db-rw.re8ch.com.": "db-rw"}
REGION = next((item["region"] for item in CANDIDATES if item["id"] == NODE), "unknown")
LOCK = threading.Lock()
HEALTH = {service: {} for service in SERVICES.values()}


def bind_address():
    configured = os.getenv("BIND_ADDRESS", "").strip()
    if configured:
        return configured
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("1.1.1.1", 53))
        return sock.getsockname()[0]
    finally:
        sock.close()


def probe(candidate, service, url):
    parsed = urllib.parse.urlparse(url)
    started = time.monotonic()
    status = 0
    failure = ""
    try:
        context = ssl.create_default_context()
        probe_ip = candidate.get("localIp", candidate["ip"]) if candidate["id"] == NODE else candidate["ip"]
        sock = socket.create_connection((probe_ip, 443), timeout=3)
        tls = context.wrap_socket(sock, server_hostname=parsed.hostname)
        connection = http.client.HTTPConnection(parsed.hostname, timeout=4)
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
        if 200 <= status < 400 or status in (401, 403):
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
    threads = []
    for candidate in CANDIDATES:
        for service, url in candidate.get("probes", {}).items():
            thread = threading.Thread(target=probe, args=(candidate, service, url), daemon=True)
            thread.start()
            threads.append(thread)
    for thread in threads:
        thread.join(timeout=5)


def probe_loop():
    while True:
        probe_all()
        time.sleep(10)


def ranked(service):
    with LOCK:
        snapshot = dict(HEALTH.get(service, {}))
    result = []
    for candidate in CANDIDATES:
        observed = snapshot.get(candidate["id"], {})
        ready = bool(observed.get("ready"))
        score = 0
        if ready:
            score = 70 - int(candidate.get("priority", 0)) - min(int(observed.get("latencyMs", 0)) // 20, 20)
            if candidate["id"] == NODE:
                score += 20
            if candidate["region"] == REGION:
                score += 10
        result.append({
            "id": candidate["id"], "region": candidate["region"], "ip": candidate["ip"],
            "score": score, "state": "ready" if ready else "unavailable",
            "statusCode": observed.get("statusCode", 0), "latencyMs": observed.get("latencyMs", 0),
            "observedAt": observed.get("observedAt", 0), "reason": observed.get("failure", ""),
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
        selected = next((item for item in ranked(service) if item["state"] == "ready"), None)
        if selected:
            answers.append(rr(name, 1, 30, ipaddress.ip_address(selected["ip"]).packed))
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
        if service not in ("api", "db-ro", "db-rw"):
            self.send_error(400, "service must be api, db-ro, db-rw, or db with mode")
            return
        candidates = ranked(service)
        selected = next((item["id"] for item in candidates if item["state"] == "ready"), "")
        payload = json.dumps({
            "version": 1, "generation": int(time.time() // 10 * 10), "service": service,
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
    probe_all()
    threading.Thread(target=probe_loop, daemon=True).start()
    dns_bind = bind_address()
    udp = socketserver.ThreadingUDPServer((dns_bind, 53), UDPHandler)
    tcp = socketserver.ThreadingTCPServer((dns_bind, 53), TCPHandler)
    threading.Thread(target=udp.serve_forever, daemon=True).start()
    threading.Thread(target=tcp.serve_forever, daemon=True).start()
    print(f"node={NODE} region={REGION} dns={dns_bind}:53 http=:8080", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), HTTPHandler).serve_forever()


if __name__ == "__main__":
    main()
