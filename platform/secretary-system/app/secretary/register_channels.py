from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request
import uuid
from typing import Any

from .interaction import utc_now


def post_json(url: str, body: dict[str, Any], *, signed: bool = False) -> None:
    raw = json.dumps(body, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json"}
    if signed:
        timestamp = str(int(time.time()))
        signature = hmac.new(
            os.environ["SECRETARY_WEBHOOK_SECRET"].encode(), timestamp.encode() + b"." + raw, hashlib.sha256,
        ).hexdigest()
        headers.update({"X-Secretary-Timestamp": timestamp, "X-Secretary-Signature": signature})
    request = urllib.request.Request(url, data=raw, method="POST", headers=headers)
    with urllib.request.urlopen(request, timeout=5) as response:
        response.read()


def retry(url: str, body: dict[str, Any], *, signed: bool = False) -> None:
    for attempt in range(60):
        try:
            post_json(url, body, signed=signed)
            return
        except Exception:
            if attempt == 59:
                raise
            time.sleep(2)


def declaration(source: dict[str, str], target: dict[str, str], relation: str,
                channel: dict[str, Any], suffix: str) -> dict[str, Any]:
    endpoint_id = channel["endpoint_id"]
    return {
        "declaration_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"re8ch:secretary:{endpoint_id}:{suffix}")),
        "source": source, "target": target, "relation": relation,
        "owner_id": str(channel["owner"]), "declared_at": utc_now(),
        "idempotency_key": f"channel-registry-v2:{endpoint_id}:{suffix}",
        "metadata": {
            "purpose": str(channel["purpose"]), "severity": ",".join(channel["severity"]),
            "service": str(channel["service"]), "route_name": str(channel["incidentrelay"]["route_name"]),
        },
    }


def main() -> None:
    channels = json.loads(os.environ["SECRETARY_CHANNEL_REGISTRY"])
    secretary_url = os.environ.get("SECRETARY_API_URL", "http://secretary-api").rstrip("/")
    for channel in channels:
        endpoint_id = channel["endpoint_id"]
        restate_url = "http://restate:8080/ChannelObject/" + urllib.parse.quote(endpoint_id, safe="") + "/observe"
        retry(restate_url, {key: value for key, value in channel.items() if key != "endpoint_id"})
        route_id = str(channel["incidentrelay"]["route_id"])
        severity = channel["severity"][0]
        declarations = [
            declaration(
                {"kind": "endpoint", "id": f"alertmanager-receiver:incidentrelay-{severity}", "runtime_id": "re8ch-alertmanager"},
                {"kind": "endpoint", "id": f"incidentrelay-route:{route_id}", "runtime_id": "incidentrelay"},
                "routes_to", channel, "alertmanager-to-incidentrelay",
            ),
            declaration(
                {"kind": "endpoint", "id": f"incidentrelay-route:{route_id}", "runtime_id": "incidentrelay"},
                {"kind": "endpoint", "id": endpoint_id, "runtime_id": "telegram"},
                "delivers_to", channel, "incidentrelay-to-telegram",
            ),
        ]
        for item in declarations:
            retry(f"{secretary_url}/v1/topology/declarations", item, signed=True)


if __name__ == "__main__":
    main()
