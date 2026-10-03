"""Measure real DNS reachability from each control node to every DNS EndpointSlice IP."""

import concurrent.futures
import http.server
import json
import os
import random
import socket
import ssl
import struct
import threading
import time
import urllib.parse
import urllib.request

NODE = os.environ["NODE_NAME"]
API = "https://%s:%s" % (os.environ["KUBERNETES_SERVICE_HOST"], os.environ["KUBERNETES_SERVICE_PORT"])
TOKEN = open("/var/run/secrets/kubernetes.io/serviceaccount/token", encoding="utf-8").read().strip()
CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
NAME = "kubernetes.default.svc.cluster.local"
QUESTION = b"".join(bytes([len(part)]) + part.encode() for part in NAME.split(".")) + b"\x00\x00\x01\x00\x01"
metrics = []
lock = threading.Lock()


def endpoints():
    selector = urllib.parse.quote("kubernetes.io/service-name=advanced-fabric-dns")
    url = API + "/apis/discovery.k8s.io/v1/namespaces/kube-system/endpointslices?labelSelector=" + selector
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(request, context=ssl.create_default_context(cafile=CA), timeout=3) as response:
        items = json.load(response)["items"]
    return [(ip, endpoint.get("nodeName", "unknown"), endpoint.get("conditions", {}).get("ready") is True)
            for item in items for endpoint in item.get("endpoints", []) for ip in endpoint.get("addresses", [])]


def query(ip, protocol):
    transaction = random.randrange(65536)
    packet = struct.pack("!HHHHHH", transaction, 0x0100, 1, 0, 0, 0) + QUESTION
    kind = socket.SOCK_DGRAM if protocol == "udp" else socket.SOCK_STREAM
    with socket.socket(socket.AF_INET, kind) as connection:
        connection.settimeout(2)
        connection.connect((ip, 53))
        source = connection.getsockname()[0]
        if protocol == "udp":
            connection.send(packet)
            response = connection.recv(4096)
        else:
            connection.sendall(struct.pack("!H", len(packet)) + packet)
            length = connection.recv(2)
            if len(length) != 2:
                return 0, source
            remaining = struct.unpack("!H", length)[0]
            response = b""
            while len(response) < remaining:
                chunk = connection.recv(remaining - len(response))
                if not chunk:
                    return 0, source
                response += chunk
    good = (len(response) >= 12 and struct.unpack("!H", response[:2])[0] == transaction
            and response[2] & 0x80 and response[3] & 0x0f == 0)
    return int(bool(good)), source


def sample(target):
    ip, node, ready = target
    result = []
    for protocol in ("udp", "tcp"):
        try:
            success, source = query(ip, protocol)
        except (OSError, ValueError):
            success, source = 0, "unknown"
        result.append((ip, node, ready, protocol, success, source))
    return result


def loop():
    global metrics
    while True:
        now = int(time.time())
        try:
            targets = endpoints()
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                rows = [row for group in pool.map(sample, targets) for row in group]
            lines = ['re8ch_dns_probe_discovery_up{source_node="%s"} 1' % NODE]
            for ip, node, ready, protocol, success, source in rows:
                labels = ('source_node="%s",target_node="%s",target_ip="%s",endpoint_ready="%s",'
                          'protocol="%s",source_ip="%s"') % (NODE, node, ip, str(ready).lower(), protocol, source)
                lines.append("re8ch_dns_peer_query_success{%s} %d" % (labels, success))
            lines.append('re8ch_dns_probe_last_run_seconds{source_node="%s"} %d' % (NODE, now))
        except (OSError, ValueError, KeyError, TimeoutError) as exc:
            print("DNS endpoint discovery failed: %s" % exc, flush=True)
            lines = ['re8ch_dns_probe_discovery_up{source_node="%s"} 0' % NODE]
        with lock:
            metrics = lines
        time.sleep(15)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_error(404)
            return
        with lock:
            body = ("\n".join(metrics) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


threading.Thread(target=loop, daemon=True).start()
http.server.ThreadingHTTPServer(("0.0.0.0", 9838), Handler).serve_forever()
