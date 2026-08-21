"""Mobile egress catalog projection and authenticated control-plane service."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ETLD1 = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode()


def signed_envelope(payload: dict[str, Any], key: Ed25519PrivateKey, key_id: str) -> dict[str, Any]:
    raw = canonical_json(payload)
    return {
        "algorithm": "Ed25519",
        "keyID": key_id,
        "payload": base64.b64encode(raw).decode(),
        "signature": base64.b64encode(key.sign(raw)).decode(),
    }


def project_mobile_egresses(
    fabric_nodes: list[dict[str, Any]],
    mihomo_statuses: list[dict[str, Any]],
    gateways: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project explicit gateways; node inventory alone never creates an exit."""
    nodes = {item["name"]: item for item in fabric_nodes}
    statuses = {item["node"]: item for item in mihomo_statuses}
    result: list[dict[str, Any]] = []
    for gateway in gateways:
        if not gateway.get("enabled", True) or gateway.get("draining", False):
            continue
        node = nodes.get(gateway.get("nodeName"))
        status = statuses.get(gateway.get("nodeName"))
        if not node or not status:
            continue
        if node.get("role") != "spine" and not gateway.get("allowLeaf", False):
            continue
        capability = gateway.get("capability")
        provider_ready = (
            capability == "cn" and status.get("isCnExit") is True
        ) or (
            capability == "global" and status.get("isGlobalExit") is True
        )
        if not provider_ready or status.get("consumer", {}).get("ok") is not True:
            continue
        transports = [
            item for item in gateway.get("transports", [])
            if item.get("type") in {"socks5", "socks5-tls"}
            and item.get("host") and 0 < int(item.get("port", 0)) < 65536
        ]
        if not transports:
            continue
        result.append({
            "id": gateway["id"],
            "nodeRef": node["name"],
            "area": gateway.get("area", "CN" if str(node.get("region", "")).startswith("cn-") else "GLOBAL"),
            "region": node.get("region", "unknown"),
            "capability": capability,
            "role": node.get("role"),
            "asn": int(node["asn"]),
            "capacityMbps": int(gateway.get("capacityMbps", node.get("uplinkMbps", 1))),
            "monthlyTrafficLimitGiB": node.get("monthlyTrafficLimitGiB"),
            "load": float(gateway.get("load", 0)),
            "health": {
                "ready": True,
                "latencyMs": status.get("consumer", {}).get("latencyMs"),
                "observedAt": status.get("timestamp"),
            },
            "transports": transports,
        })
    return sorted(result, key=lambda item: item["id"])


@dataclass(frozen=True)
class Identity:
    subject: str
    email: str = ""


class MobileService:
    def __init__(
        self,
        catalog_loader: Callable[[], list[dict[str, Any]]],
        signing_key: Ed25519PrivateKey,
        credential_secret: bytes,
        key_id: str = "mobile-egress-2026-01",
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if len(credential_secret) < 32:
            raise ValueError("credential secret must contain at least 32 bytes")
        self.catalog_loader = catalog_loader
        self.signing_key = signing_key
        self.credential_secret = credential_secret
        self.key_id = key_id
        self.now = now or (lambda: datetime.now(timezone.utc))

    def enroll(self, identity: Identity, body: dict[str, Any]) -> dict[str, Any]:
        public_key = str(body.get("devicePublicKey", "")).strip()
        platform = body.get("platform")
        if platform not in {"ios", "macos"} or len(public_key) < 32:
            raise ValueError("platform and devicePublicKey are required")
        device_id = hashlib.sha256(f"{identity.subject}\0{public_key}".encode()).hexdigest()[:32]
        return {"deviceID": device_id, "platform": platform, "enrolled": True}

    def bootstrap(self, device_id: str) -> dict[str, Any]:
        issued = self.now().replace(microsecond=0)
        payload = {
            "schemaVersion": "re8ch.mobile-bootstrap/v1",
            "deviceID": device_id,
            "issuedAt": issued.isoformat().replace("+00:00", "Z"),
            "expiresAt": (issued + timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
            "catalogRefreshSeconds": 30,
            "credentialRefreshSeconds": 2700,
            "dns": {"defaultMode": "tunnel-doh", "allowSystemFallback": True},
            "fallback": {"default": "direct", "allowed": ["direct", "best-remaining"]},
        }
        return signed_envelope(payload, self.signing_key, self.key_id)

    def egresses(self) -> tuple[dict[str, Any], str]:
        issued = self.now().replace(microsecond=0)
        payload = {
            "schemaVersion": "re8ch.mobile-egress-catalog/v1",
            "issuedAt": issued.isoformat().replace("+00:00", "Z"),
            "expiresAt": (issued + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
            "egresses": self.catalog_loader(),
        }
        envelope = signed_envelope(payload, self.signing_key, self.key_id)
        return envelope, hashlib.sha256(canonical_json(envelope)).hexdigest()

    def credentials(self, identity: Identity, device_id: str, egress_id: str) -> dict[str, Any]:
        candidate = next((item for item in self.catalog_loader() if item["id"] == egress_id), None)
        if candidate is None:
            raise LookupError("egress is not eligible")
        expires = int(self.now().timestamp()) + 3600
        claims = {"sub": identity.subject, "device": device_id, "egress": egress_id, "exp": expires}
        raw = base64.urlsafe_b64encode(canonical_json(claims)).rstrip(b"=")
        signature = hmac.new(self.credential_secret, raw, hashlib.sha256).digest()
        token = raw.decode() + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
        return {"egressID": egress_id, "credential": token, "expiresAt": expires,
                "transports": candidate["transports"]}

    def accept_telemetry(self, device_id: str, batch: dict[str, Any]) -> dict[str, int]:
        samples = batch.get("samples")
        if not isinstance(samples, list) or len(samples) > 500:
            raise ValueError("samples must be an array with at most 500 entries")
        accepted = 0
        for item in samples:
            domain = str(item.get("eTLDPlusOne", "")).lower()
            if domain and not ETLD1.fullmatch(domain):
                raise ValueError("telemetry may contain eTLD+1 only")
            forbidden = {"url", "path", "query", "payload", "destinationIP"}.intersection(item)
            if forbidden:
                raise ValueError("telemetry contains forbidden detail")
            if item.get("deviceID", device_id) != device_id:
                raise ValueError("device mismatch")
            accepted += 1
        return {"accepted": accepted}
