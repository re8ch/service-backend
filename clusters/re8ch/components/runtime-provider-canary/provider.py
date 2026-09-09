#!/usr/bin/env python3
"""Small provider used to qualify the runtime gateway contract."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROVIDER_NAME = os.getenv("PROVIDER_NAME", "unknown")
PROVIDER_KIND = os.getenv("PROVIDER_KIND", "container")
PORT = int(os.getenv("PORT", "8080"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def reply(self, status, payload):
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            return self.reply(200, {"status": "ready", "provider": PROVIDER_NAME})
        return self.reply(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        self.rfile.read(length)
        return self.reply(200, {
            "provider": PROVIDER_NAME,
            "kind": PROVIDER_KIND,
            "workload": self.path.removeprefix("/invoke/"),
        })


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
