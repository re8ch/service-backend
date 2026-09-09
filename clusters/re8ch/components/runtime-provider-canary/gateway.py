#!/usr/bin/env python3
"""Protocol-neutral runtime provider canary gateway."""

import json
import os
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROVIDERS = tuple(
    item.strip() for item in os.getenv(
        "RUNTIME_PROVIDERS",
        "http://runtime-provider-local:8080,http://runtime-provider-external-simulator:8080",
    ).split(",") if item.strip()
)
TIMEOUT = float(os.getenv("PROVIDER_TIMEOUT_SECONDS", "2"))
PORT = int(os.getenv("PORT", "8080"))
COUNTERS = {"requests": 0, "fallbacks": 0, "failures": 0}


def invoke(path, body, headers, skip_primary=False):
    errors = []
    providers = PROVIDERS[1:] if skip_primary else PROVIDERS
    for index, provider in enumerate(providers):
        try:
            request = urllib.request.Request(
                provider + path,
                data=body,
                method="POST",
                headers={
                    "Content-Type": headers.get("Content-Type", "application/json"),
                    "X-Request-ID": headers.get("X-Request-ID", ""),
                },
            )
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                if index or skip_primary:
                    COUNTERS["fallbacks"] += 1
                return response.status, response.headers, response.read(), provider
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            errors.append(f"{provider}: {exc}")
    COUNTERS["failures"] += 1
    return 503, {}, json.dumps({"error": "all runtime providers unavailable", "details": errors}).encode(), "none"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def send_body(self, status, body, content_type="application/json", provider=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if provider:
            self.send_header("X-Re8ch-Runtime-Provider", provider)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            return self.send_body(200, b'{"status":"ok"}')
        if self.path == "/metrics":
            body = "".join(
                f"re8ch_runtime_gateway_{name}_total {value}\n"
                for name, value in COUNTERS.items()
            ).encode()
            return self.send_body(200, body, "text/plain; version=0.0.4")
        return self.send_body(404, b'{"error":"not found"}')

    def do_POST(self):
        if not self.path.startswith("/invoke/"):
            return self.send_body(404, b'{"error":"expected /invoke/{workload}"}')
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 1024 * 1024:
            return self.send_body(413, b'{"error":"request too large"}')
        COUNTERS["requests"] += 1
        started = time.monotonic()
        status, headers, body, provider = invoke(
            self.path,
            self.rfile.read(length),
            self.headers,
            self.headers.get("X-Re8ch-Canary-Fail-Primary") == "true",
        )
        self.log_message("provider=%s status=%s duration_ms=%d", provider, status, (time.monotonic() - started) * 1000)
        self.send_body(status, body, headers.get("Content-Type", "application/json"), provider)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
