#!/usr/bin/env python3
"""HTTP publisher for generated MikroTik RouterOS rule files."""

from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote


class Handler(SimpleHTTPRequestHandler):
    server_version = "re8ch-mikrotik-rule-publisher/0.1"

    def do_GET(self) -> None:
        if self.path in {"/healthz", "/readyz"}:
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}\n')
            return
        super().do_GET()

    def translate_path(self, path: str) -> str:
        root: Path = self.server.root_path  # type: ignore[attr-defined]
        clean = unquote(path.split("?", 1)[0].split("#", 1)[0]).lstrip("/")
        if clean in {"", "/"}:
            clean = "index.json"
        return str(root / clean)

    def end_headers(self) -> None:
        self.send_header("cache-control", "no-store")
        super().end_headers()


def write_index(root: Path) -> None:
    summary_path = root / "summary.json"
    summary = {}
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text())
        except Exception:
            summary = {"warning": "summary.json exists but is not valid JSON"}
    index = {
        "service": "re8ch-mikrotik-rule-publisher",
        "files": {
            "address_lists": "/re8ch-address-lists.rsc",
            "split_routing_canary": "/re8ch-split-routing-canary.rsc",
            "split_routing_employee": "/re8ch-split-routing-employee.rsc",
            "manual_kit": "/manual-kit/",
            "summary": "/summary.json",
        },
        "summary": summary,
    }
    (root / "index.json").write_text(json.dumps(index, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/data/public/rsc")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    mimetypes.add_type("text/plain", ".rsc")
    write_index(root)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.root_path = root  # type: ignore[attr-defined]
    print(f"serving {root} on {args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
