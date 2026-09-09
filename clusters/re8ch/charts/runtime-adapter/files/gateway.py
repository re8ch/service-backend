#!/usr/bin/env python3
"""Runtime Adapter: provider routing with EndpointSlice/Fabric awareness."""

import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API = "https://kubernetes.default.svc"
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
PROVIDERS_FILE = os.getenv("PROVIDERS_FILE", "/runtime/providers.json")
TIMEOUT = float(os.getenv("PROVIDER_TIMEOUT_SECONDS", "30"))
MAX_REQUEST = int(os.getenv("MAX_REQUEST_BYTES", str(16 * 1024 * 1024)))
COUNTERS = {"requests": 0, "fallbacks": 0, "failures": 0, "fabric_discovery_failures": 0}
LOCK = threading.Lock()
CACHE = {"fabric": ({}, 0.0), "endpoints": {}}


def load_providers():
    with open(PROVIDERS_FILE, encoding="utf-8") as stream:
        providers = json.load(stream)
    return [item for item in providers if item.get("enabled")]


def api_get(path):
    token = open(TOKEN_PATH, encoding="utf-8").read().strip()
    request = urllib.request.Request(API + path, headers={"Authorization": f"Bearer {token}"})
    context = ssl.create_default_context(cafile=CA_PATH)
    with urllib.request.urlopen(request, context=context, timeout=3) as response:
        return json.load(response)


def fabric_capacity():
    cached, expires = CACHE["fabric"]
    if time.monotonic() < expires:
        return cached
    capacities = {}
    try:
        data = api_get("/apis/networking.re8ch.com/v1alpha1/advancedfabrics")
        for fabric in data.get("items", []):
            for node in fabric.get("spec", {}).get("nodes", []):
                name = node.get("name") or node.get("nodeName")
                if name:
                    capacities[name] = float(node.get("uplinkMbps", 1) or 1)
    except Exception:
        with LOCK:
            COUNTERS["fabric_discovery_failures"] += 1
    CACHE["fabric"] = (capacities, time.monotonic() + 5)
    return capacities


def endpoints(provider):
    service = provider.get("service")
    if not service:
        url = provider.get("url", "")
        return [(url.rstrip("/"), "external", 1.0)] if url.startswith("https://") else []
    namespace, name = service["namespace"], service["name"]
    cache_key = (namespace, name, service["port"])
    cached, expires = CACHE["endpoints"].get(cache_key, (None, 0.0))
    if cached is not None and time.monotonic() < expires:
        return cached
    selector = urllib.parse.quote(f"kubernetes.io/service-name={name}")
    path = f"/apis/discovery.k8s.io/v1/namespaces/{namespace}/endpointslices?labelSelector={selector}"
    capacities = fabric_capacity()
    choices = []
    try:
        for item in api_get(path).get("items", []):
            ports = item.get("ports") or []
            port = next((entry.get("port") for entry in ports if entry.get("port")), service["port"])
            for endpoint in item.get("endpoints", []):
                if endpoint.get("conditions", {}).get("ready", True) is False:
                    continue
                node = endpoint.get("nodeName", "unknown")
                for address in endpoint.get("addresses", []):
                    choices.append((f"http://{address}:{port}", node, capacities.get(node, 1.0)))
    except Exception:
        with LOCK:
            COUNTERS["fabric_discovery_failures"] += 1
    if not choices:
        choices.append((f"http://{name}.{namespace}.svc.cluster.local:{service['port']}", "service", 1.0))
    choices = sorted(choices, key=lambda item: (-item[2], item[1], item[0]))
    CACHE["endpoints"][cache_key] = (choices, time.monotonic() + 5)
    return choices


def candidates(workload):
    matched = []
    for provider in load_providers():
        prefixes = provider.get("workloadPrefixes", [])
        prefix = next((value for value in prefixes if workload.startswith(value)), None)
        if prefix is None:
            continue
        suffix = workload[len(prefix):]
        path = provider.get("pathPrefix", "/") + suffix
        for base, node, capacity in endpoints(provider):
            matched.append((int(provider.get("priority", 0)), capacity, provider, base + path, node))
    return sorted(matched, key=lambda item: (-item[0], -item[1], item[2]["name"], item[4]))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def body(self, status, body, content_type="application/json", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            return self.body(200, b'{"status":"ok"}')
        if self.path == "/metrics":
            with LOCK:
                snapshot = dict(COUNTERS)
            payload = "".join(f"re8ch_runtime_adapter_{key}_total {value}\n" for key, value in snapshot.items()).encode()
            return self.body(200, payload, "text/plain; version=0.0.4")
        if self.path == "/providers":
            summary = [{"name": item[2]["name"], "kind": item[2]["kind"], "node": item[4], "capacity": item[1]}
                       for prefix in {p for provider in load_providers() for p in provider.get("workloadPrefixes", [])}
                       for item in candidates(prefix)]
            return self.body(200, json.dumps({"providers": summary}, separators=(",", ":")).encode())
        return self.body(404, b'{"error":"not found"}')

    def do_POST(self):
        if not self.path.startswith("/invoke/"):
            return self.body(404, b'{"error":"expected /invoke/{workload}"}')
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > MAX_REQUEST:
            return self.body(413, b'{"error":"request too large"}')
        payload = self.rfile.read(length)
        workload = urllib.parse.urlsplit(self.path).path.removeprefix("/invoke/")
        choices = candidates(workload)
        if self.headers.get("X-Re8ch-Canary-Fail-Primary") == "true":
            choices = choices[1:]
        if not choices:
            return self.body(503, b'{"error":"no eligible runtime provider"}')
        allow_fallback = bool(self.headers.get("Idempotency-Key") or self.headers.get("X-Re8ch-Canary-Fail-Primary"))
        with LOCK:
            COUNTERS["requests"] += 1
        errors = []
        for index, (_, _, provider, url, node) in enumerate(choices):
            headers = {key: value for key, value in self.headers.items()
                       if key.lower() not in {"host", "content-length", "connection"}}
            auth_env = provider.get("authorizationEnv")
            if auth_env and os.getenv(auth_env):
                headers["Authorization"] = os.environ[auth_env]
            headers["X-Re8ch-Runtime-Provider"] = provider["name"]
            headers["X-Re8ch-Runtime-Node"] = node
            try:
                request = urllib.request.Request(url, data=payload, method="POST", headers=headers)
                response = urllib.request.urlopen(request, timeout=TIMEOUT)
            except urllib.error.HTTPError as response:
                pass
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                errors.append(f"{provider['name']}: {exc}")
                if not allow_fallback:
                    break
                continue
            if index:
                with LOCK:
                    COUNTERS["fallbacks"] += 1
            self.send_response(response.status)
            has_length = False
            for key, value in response.headers.items():
                if key.lower() == "content-length":
                    has_length = True
                    self.send_header(key, value)
                elif key.lower() not in {"connection", "transfer-encoding"}:
                    self.send_header(key, value)
            self.send_header("X-Re8ch-Runtime-Provider", provider["name"])
            self.send_header("X-Re8ch-Runtime-Node", node)
            if not has_length:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
            response.close()
            return
        with LOCK:
            COUNTERS["failures"] += 1
        self.body(503, json.dumps({"error": "runtime provider unavailable", "details": errors}).encode())


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
