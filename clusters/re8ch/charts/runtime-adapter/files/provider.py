#!/usr/bin/env python3
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NAME = os.getenv("PROVIDER_NAME", "unknown")
KIND = os.getenv("PROVIDER_KIND", "container")


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
        self.reply(200, {"status": "ready", "provider": NAME}) if self.path == "/healthz" else self.reply(404, {"error": "not found"})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
        self.reply(200, {"provider": NAME, "kind": KIND, "workload": self.path.removeprefix("/invoke/")})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()

