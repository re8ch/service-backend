#!/usr/bin/env python3
"""Read-only Macmini DevOps context endpoint; mutations continue through MCP/CI."""

from __future__ import annotations

import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(os.getenv("RE8CH_SERVICE_BACKEND", "/Volumes/A1/service-backend"))
sys.path.insert(0, str(ROOT / "operations/workspace-controller"))
from workspace import Controller, WorkspaceError  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if not ROOT.exists():
            return self.reply(503, {"status": "a1-unmounted"})
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            return self.reply(200, {
                "status": "ok", "role": "macmini-devops-primary", "root": str(ROOT)
            })
        try:
            controller = Controller(ROOT)
            if parsed.path == "/v1/context":
                query = parse_qs(parsed.query)
                body = controller.context((query.get("module") or [None])[0], (query.get("q") or [None])[0])
            elif parsed.path == "/v1/status":
                body = controller.status()
            elif parsed.path == "/v1/memory-audit":
                body = controller.memory_audit()
            else:
                return self.reply(404, {"error": "not_found"})
            self.reply(200, body)
        except WorkspaceError as exc:
            self.reply(400, {"error": str(exc)})
        except Exception as exc:  # Keep the HTTP contract even if workspace schema loading fails.
            print(json.dumps({
                "event": "handler_error", "path": parsed.path,
                "errorType": type(exc).__name__, "traceback": traceback.format_exc(),
            }), file=sys.stderr)
            self.reply(500, {"error": "internal_error"})

    def do_POST(self) -> None:
        self.reply(405, {"error": "read_only_use_mcp_or_ci"})

    def log_message(self, fmt: str, *args) -> None:
        print(json.dumps({"event": "http", "remote": self.client_address[0], "message": fmt % args}))

    def reply(self, status: int, body) -> None:
        payload = json.dumps(body, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(os.getenv("RE8CH_DEVOPS_PORT", "19640"))), Handler).serve_forever()
