#!/usr/bin/env python3
"""Adapter: provider routing with EndpointSlice/Fabric awareness."""

import json
import os
import re
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
COUNTERS = {"requests": 0, "fallbacks": 0, "failures": 0, "fabric_discovery_failures": 0,
            "nuclio_discovery_failures": 0, "nuclio_scaled_from_zero": 0}
LOCK = threading.Lock()
CACHE = {"fabric": ({}, 0.0), "endpoints": {}, "nuclio": {}, "nuclio_services": {}}
ENDPOINT_STATE = {}
PROVIDER_COUNTERS = {}
DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")


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
    discovered = False
    try:
        items = api_get(path).get("items", [])
        discovered = True
        for item in items:
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
    if not choices and (not discovered or provider.get("allowNoReadyEndpoints")):
        choices.append((f"http://{name}.{namespace}.svc.cluster.local:{service['port']}", "service", 1.0))
    choices = sorted(choices, key=lambda item: (-item[2], item[1], item[0]))
    CACHE["endpoints"][cache_key] = (choices, time.monotonic() + 5)
    return choices


def nuclio_function(provider, namespace, name):
    key = (namespace, name)
    cached, expires, stale_until = CACHE["nuclio"].get(key, (None, 0.0, 0.0))
    now = time.monotonic()
    if cached is not None and now < expires:
        return cached
    discovery = provider.get("discovery", {})
    try:
        path = "/apis/nuclio.io/v1beta1/namespaces/%s/nucliofunctions/%s" % (
            urllib.parse.quote(namespace, safe=""), urllib.parse.quote(name, safe=""))
        function = api_get(path)
        ttl = float(discovery.get("cacheSeconds", 5))
        stale = float(discovery.get("staleSeconds", 60))
        CACHE["nuclio"][key] = (function, now + ttl, now + stale)
        return function
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            ttl = float(discovery.get("cacheSeconds", 5))
            stale = float(discovery.get("staleSeconds", 60))
            CACHE["nuclio"][key] = ({}, now + ttl, now + stale)
            return {}
        with LOCK:
            COUNTERS["nuclio_discovery_failures"] += 1
        if cached is not None and now < stale_until:
            return cached
        return None
    except Exception:
        with LOCK:
            COUNTERS["nuclio_discovery_failures"] += 1
        if cached is not None and now < stale_until:
            return cached
        return None


def nuclio_services(provider, namespace, function_name):
    key = (namespace, function_name)
    cached, expires = CACHE["nuclio_services"].get(key, (None, 0.0))
    now = time.monotonic()
    if cached is not None and now < expires:
        return cached
    try:
        path = "/api/v1/namespaces/%s/services" % urllib.parse.quote(namespace, safe="")
        found = []
        for service in api_get(path).get("items", []):
            metadata = service.get("metadata", {})
            labels = metadata.get("labels", {})
            selector = service.get("spec", {}).get("selector", {})
            selected = selector.get("nuclio.io/function-name")
            labelled = labels.get("nuclio.io/function-name")
            if function_name not in {selected, labelled}:
                continue
            ports = service.get("spec", {}).get("ports") or []
            port = next((item.get("port") for item in ports if item.get("port")), 8080)
            found.append({"name": metadata["name"], "namespace": namespace, "port": port})
        ttl = float(provider.get("discovery", {}).get("cacheSeconds", 5))
        CACHE["nuclio_services"][key] = (found, now + ttl)
        return found
    except Exception:
        with LOCK:
            COUNTERS["nuclio_discovery_failures"] += 1
        return cached or []


def nuclio_candidates(workload, provider, prefix):
    # Contract: nuclio/<namespace>/<function>/<function-relative-path>
    parts = workload[len(prefix):].split("/", 2)
    if len(parts) < 2 or not all(parts[:2]):
        return []
    namespace, function_name = parts[:2]
    if not DNS_LABEL.fullmatch(namespace) or not DNS_LABEL.fullmatch(function_name):
        return []
    relative_path = "/" + (parts[2] if len(parts) == 3 else "")
    allowed = provider.get("discovery", {}).get("namespaces", ["*"])
    if "*" not in allowed and namespace not in allowed:
        return []
    function = nuclio_function(provider, namespace, function_name)
    state = function.get("status", {}).get("state", "") if function else "serviceDiscovered"
    if function:
        include_zero = provider.get("discovery", {}).get("includeScaledToZero", True)
        if state != "ready" and not (include_zero and state == "scaledToZero"):
            return []
    invocation_urls = function.get("status", {}).get("internalInvocationUrls") or [] if function else []
    services = []
    for value in invocation_urls:
        parsed = urllib.parse.urlsplit(value if "://" in value else "http://" + value)
        host = parsed.hostname or ""
        labels = host.split(".")
        if len(labels) >= 3 and labels[2] == "svc" and DNS_LABEL.fullmatch(labels[0]) and DNS_LABEL.fullmatch(labels[1]):
            services.append({"name": labels[0], "namespace": labels[1], "port": parsed.port or 8080})
    services.extend(nuclio_services(provider, namespace, function_name))
    services = list({(item["namespace"], item["name"], item["port"]): item for item in services}.values())
    if not services:
        return []
    dynamic = dict(provider)
    dynamic["nuclioFunction"] = namespace + "/" + function_name
    dynamic["nuclioState"] = state
    result = []
    for service in services:
        dynamic_service = dict(dynamic, service=service, allowNoReadyEndpoints=(state == "scaledToZero"))
        for base, node, capacity in endpoints(dynamic_service):
            result.append((int(provider.get("priority", 0)), capacity, dynamic, base + relative_path, node))
    return result


def candidates(workload):
    matched = []
    for provider in load_providers():
        prefixes = provider.get("workloadPrefixes", [])
        prefix = next((value for value in prefixes if workload.startswith(value)), None)
        if prefix is None:
            continue
        if provider.get("kind") == "nuclio":
            matched.extend(nuclio_candidates(workload, provider, prefix))
            continue
        suffix = workload[len(prefix):]
        path = provider.get("pathPrefix", "/") + suffix
        for base, node, capacity in endpoints(provider):
            matched.append((int(provider.get("priority", 0)), capacity, provider, base + path, node))
    def rank(item):
        runtime = ENDPOINT_STATE.get(item[3], {})
        return (-item[0], runtime.get("inflight", 0), runtime.get("ewma_ms", 0), -item[1], item[2]["name"], item[4])
    return sorted(matched, key=rank)


def metric_key(provider, outcome):
    return (provider.get("name", "unknown"), provider.get("kind", "unknown"),
            provider.get("nuclioFunction", ""), outcome)


def observe(provider, outcome, elapsed_ms, url):
    with LOCK:
        key = metric_key(provider, outcome)
        PROVIDER_COUNTERS[key] = PROVIDER_COUNTERS.get(key, 0) + 1
        state = ENDPOINT_STATE.setdefault(url, {"inflight": 0, "ewma_ms": elapsed_ms})
        state["ewma_ms"] = elapsed_ms if not state.get("ewma_ms") else state["ewma_ms"] * 0.8 + elapsed_ms * 0.2


def prometheus_metrics():
    with LOCK:
        counters = dict(COUNTERS)
        providers = dict(PROVIDER_COUNTERS)
        endpoints_state = {key: dict(value) for key, value in ENDPOINT_STATE.items()}
    lines = [f"re8ch_adapter_{key}_total {value}\n" for key, value in counters.items()]
    for (provider, kind, function, outcome), value in sorted(providers.items()):
        labels = f'provider="{provider}",kind="{kind}",function="{function}",outcome="{outcome}"'
        lines.append(f"re8ch_adapter_provider_requests_total{{{labels}}} {value}\n")
    for url, state in sorted(endpoints_state.items()):
        endpoint = urllib.parse.urlsplit(url).netloc.replace('"', '')
        lines.append(f're8ch_adapter_endpoint_inflight{{endpoint="{endpoint}"}} {state.get("inflight", 0)}\n')
        lines.append(f're8ch_adapter_endpoint_ewma_milliseconds{{endpoint="{endpoint}"}} {state.get("ewma_ms", 0):.3f}\n')
    return "".join(lines).encode()


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
            return self.body(200, prometheus_metrics(), "text/plain; version=0.0.4")
        if self.path == "/providers":
            summary = [{"name": provider["name"], "kind": provider["kind"],
                        "workloadPrefixes": provider.get("workloadPrefixes", []),
                        "dynamicDiscovery": provider.get("kind") == "nuclio"}
                       for provider in load_providers()]
            return self.body(200, json.dumps({"providers": summary}, separators=(",", ":")).encode())
        if self.path.startswith("/invoke/"):
            return self.proxy()
        return self.body(404, b'{"error":"not found"}')

    def do_POST(self):
        return self.proxy()

    def do_PUT(self):
        return self.proxy()

    def do_PATCH(self):
        return self.proxy()

    def do_DELETE(self):
        return self.proxy()

    def proxy(self):
        if not self.path.startswith("/invoke/"):
            return self.body(404, b'{"error":"expected /invoke/{workload}"}')
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > MAX_REQUEST:
            return self.body(413, b'{"error":"request too large"}')
        payload = self.rfile.read(length)
        split = urllib.parse.urlsplit(self.path)
        workload = split.path.removeprefix("/invoke/")
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
            if provider.get("nuclioFunction"):
                headers["X-Re8ch-Nuclio-Function"] = provider["nuclioFunction"]
            if split.query:
                url += ("&" if "?" in url else "?") + split.query
            started = time.monotonic()
            with LOCK:
                endpoint_state = ENDPOINT_STATE.setdefault(url, {"inflight": 0, "ewma_ms": 0})
                endpoint_state["inflight"] += 1
            try:
                request = urllib.request.Request(url, data=payload if length else None, method=self.command, headers=headers)
                response = urllib.request.urlopen(request, timeout=TIMEOUT)
            except urllib.error.HTTPError as response:
                pass
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                with LOCK:
                    ENDPOINT_STATE[url]["inflight"] = max(0, ENDPOINT_STATE[url]["inflight"] - 1)
                observe(provider, "transport_error", (time.monotonic() - started) * 1000, url)
                errors.append(f"{provider['name']}: {exc}")
                if not allow_fallback:
                    break
                continue
            with LOCK:
                ENDPOINT_STATE[url]["inflight"] = max(0, ENDPOINT_STATE[url]["inflight"] - 1)
            observe(provider, str(response.status), (time.monotonic() - started) * 1000, url)
            if provider.get("nuclioState") == "scaledToZero":
                with LOCK:
                    COUNTERS["nuclio_scaled_from_zero"] += 1
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
            if provider.get("nuclioFunction"):
                self.send_header("X-Re8ch-Nuclio-Function", provider["nuclioFunction"])
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
