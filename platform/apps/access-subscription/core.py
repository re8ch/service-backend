"""Pure helpers for the RE8CH personal access subscription service."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote, urlencode


MONTHLY_SOFT_LIMIT = 300 * 1024**3


@dataclass(frozen=True)
class Node:
    name: str
    protocol: str
    server: str
    port: int = 443


DEFAULT_NODES = (
    Node("♻️ Auto · Smart Select", "anytls", "access.re8ch.com"),
    Node("🇺🇸 US · California · AnyTLS", "anytls", "us-ca.access.re8ch.com"),
    Node("🛟 Backup · Hysteria2", "hysteria2", "backup.access.re8ch.com"),
)


def canonical_subject(user: str | None, email: str | None, aliases: dict[str, str]) -> str:
    normalized_aliases = {str(key).strip().casefold(): str(value).strip() for key, value in aliases.items()}
    for candidate in (email, user):
        value = str(candidate or "").strip()
        if value and value.casefold() in normalized_aliases:
            return normalized_aliases[value.casefold()]
    subject = str(user or email or "").strip()
    if not subject:
        raise ValueError("Dex identity required")
    return subject


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_token(secret: bytes, subject: str, device: str, ttl_days: int = 90, now: int | None = None) -> str:
    issued = int(now if now is not None else time.time())
    payload = {
        "sub": subject,
        "device": device,
        "iat": issued,
        "exp": issued + ttl_days * 86400,
        "jti": hashlib.sha256(f"{subject}\0{device}\0{issued}\0{secrets.token_hex(8)}".encode()).hexdigest()[:24],
    }
    body = b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = b64url(hmac.new(secret, body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def verify_token(secret: bytes, token: str, now: int | None = None) -> dict:
    body, signature = token.split(".", 1)
    expected = b64url(hmac.new(secret, body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise ValueError("invalid subscription token")
    payload = json.loads(b64url_decode(body))
    if int(payload["exp"]) <= int(now if now is not None else time.time()):
        raise ValueError("subscription token expired")
    return payload


def proxy_password(secret: bytes, token_id: str) -> str:
    return hmac.new(secret, f"proxy:{token_id}".encode(), hashlib.sha256).hexdigest()


def node_uri(node: Node, password: str) -> str:
    label = quote(node.name, safe="")
    query = urlencode({"sni": node.server, "insecure": "0"})
    if node.protocol == "anytls":
        return f"anytls://{password}@{node.server}:{node.port}/?{query}#{label}"
    if node.protocol == "hysteria2":
        return f"hysteria2://{password}@{node.server}:{node.port}/?{query}#{label}"
    raise ValueError(f"unsupported protocol: {node.protocol}")


def subscription_body(password: str, nodes: tuple[Node, ...] = DEFAULT_NODES) -> bytes:
    raw = "\n".join(node_uri(node, password) for node in nodes) + "\n"
    return base64.b64encode(raw.encode())


def month_end_epoch(now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    if current.month == 12:
        boundary = datetime(current.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        boundary = datetime(current.year, current.month + 1, 1, tzinfo=timezone.utc)
    return int(boundary.timestamp())


def subscription_headers(upload: int = 0, download: int = 0, now: datetime | None = None) -> dict[str, str]:
    return {
        "Subscription-Userinfo": (
            f"upload={max(0, upload)}; download={max(0, download)}; "
            f"total={MONTHLY_SOFT_LIMIT}; expire={month_end_epoch(now)}"
        ),
        "Profile-Title": "access re8ch com",
        "Profile-Update-Interval": "6",
    }
