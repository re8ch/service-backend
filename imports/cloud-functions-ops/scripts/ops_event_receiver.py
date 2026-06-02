#!/usr/bin/env python3
"""Small external webhook receiver for ops events.

Accepts generic JSON or Alertmanager-style webhooks, appends them to JSONL, and
optionally forwards a compact card to Feishu.
"""

from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import ops_watch_agent


DEFAULT_EVENTS_FILE = os.environ.get("OPS_EVENTS_FILE", "/tmp/qianwen-ops-events.jsonl")


def normalize_event(path: str, payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("alerts"), list):
        events = []
        for alert in payload["alerts"]:
            labels = alert.get("labels", {}) or {}
            annotations = alert.get("annotations", {}) or {}
            status = alert.get("status") or payload.get("status") or "unknown"
            events.append({
                "source": "alertmanager",
                "path": path,
                "status": status,
                "severity": labels.get("severity", "warning"),
                "name": labels.get("alertname") or labels.get("name") or "alert",
                "summary": annotations.get("summary") or annotations.get("description") or json.dumps(labels, ensure_ascii=False),
                "labels": labels,
                "annotations": annotations,
                "startsAt": alert.get("startsAt"),
                "endsAt": alert.get("endsAt"),
                "raw": alert,
            })
        return events
    return [{
        "source": "generic-webhook",
        "path": path,
        "status": "firing",
        "severity": "info",
        "name": "ops-event",
        "summary": json.dumps(payload, ensure_ascii=False)[:500],
        "raw": payload,
    }]


class Receiver(BaseHTTPRequestHandler):
    server_version = "qianwen-ops-event-receiver/0.1"

    def _write(self, status: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path in {"/", "/health"}:
            self._write(200, {"ok": True})
            return
        self._write(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception as exc:
            self._write(400, {"ok": False, "error": f"invalid json: {exc}"})
            return

        events = normalize_event(self.path, payload)
        with open(self.server.events_file, "a", encoding="utf-8") as f:  # type: ignore[attr-defined]
            for event in events:
                event["receivedAt"] = ops_watch_agent.now_iso()
                f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

        webhook = self.server.feishu_webhook  # type: ignore[attr-defined]
        if webhook:
            lines = []
            severity = "info"
            for event in events[:10]:
                severity = "critical" if event.get("severity") == "critical" else severity
                status = event.get("status", "event").upper()
                lines.append(f"- **{status}** `{event.get('name')}` {event.get('summary')}")
            ops_watch_agent.send_feishu(
                webhook,
                self.server.feishu_secret,  # type: ignore[attr-defined]
                f"Ops Event: {len(events)} event(s)",
                lines,
                severity,
                self.server.dry_run,  # type: ignore[attr-defined]
            )

        self._write(200, {"ok": True, "events": len(events)})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Receive ops webhooks and optionally forward them to Feishu.")
    parser.add_argument("--listen", default=os.environ.get("OPS_EVENT_LISTEN", "127.0.0.1:8787"))
    parser.add_argument("--events-file", default=DEFAULT_EVENTS_FILE)
    parser.add_argument("--feishu-webhook", default=os.environ.get("FEISHU_WEBHOOK_URL", ""))
    parser.add_argument("--feishu-secret", default=os.environ.get("FEISHU_WEBHOOK_SECRET", ""))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    host, port_raw = args.listen.rsplit(":", 1)
    server = ThreadingHTTPServer((host, int(port_raw)), Receiver)
    server.events_file = args.events_file  # type: ignore[attr-defined]
    server.feishu_webhook = args.feishu_webhook  # type: ignore[attr-defined]
    server.feishu_secret = args.feishu_secret  # type: ignore[attr-defined]
    server.dry_run = args.dry_run  # type: ignore[attr-defined]
    print(json.dumps({"listening": args.listen, "eventsFile": args.events_file}, ensure_ascii=False), flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
