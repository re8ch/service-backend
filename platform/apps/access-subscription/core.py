"""Pure helpers for the RE8CH personal access control plane."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

MONTHLY_SOFT_LIMIT = 300 * 1024**3
AUTH_MAX_STALE_SECONDS = 24 * 60 * 60
PROFILE_TITLE = "access re8ch com"


@dataclass(frozen=True)
class Publication:
    protocol: str
    server: str
    port: int
    sni: str


@dataclass(frozen=True)
class RuntimeTarget:
    service: str
    listener: str
    region: str
    qualified_mbps: int
    healthy: bool = True


@dataclass(frozen=True)
class Node:
    node_id: str
    name: str
    publication: Publication
    runtime: RuntimeTarget

    @property
    def protocol(self) -> str:
        return self.publication.protocol

    @property
    def server(self) -> str:
        return self.publication.server

    @property
    def port(self) -> int:
        return self.publication.port


DEFAULT_NODES = (
    Node("auto", "♻️ Auto · Smart Select", Publication("anytls", "access.re8ch.com", 443, "access.re8ch.com"), RuntimeTarget("personal-access-auto", "access-anytls", "auto", 100)),
    Node("us-ca", "🇺🇸 US · California · AnyTLS", Publication("anytls", "us-ca.access.re8ch.com", 443, "us-ca.access.re8ch.com"), RuntimeTarget("personal-access-us-ca", "access-anytls", "us-ca", 100)),
    Node("backup", "🛟 Backup · Hysteria2", Publication("hysteria2", "backup.access.re8ch.com", 443, "backup.access.re8ch.com"), RuntimeTarget("personal-access-hysteria2", "access-hysteria2", "auto", 100)),
)

SERVICE_RULESETS = (
    ("ai", "🤖 AI"), ("google", "🔎 Google / YouTube"), ("telegram", "✈️ Telegram"),
    ("streaming", "🎬 Streaming"), ("social", "💬 Social"), ("developer", "🧑‍💻 Developer"),
    ("apple", "🍎 Apple"), ("microsoft", "🪟 Microsoft"), ("games", "🎮 Games"),
)


def published_nodes(nodes: tuple[Node, ...] = DEFAULT_NODES) -> tuple[Node, ...]:
    return tuple(node for node in nodes if node.runtime.healthy and node.runtime.qualified_mbps >= 100)


def public_node(node: Node) -> dict:
    # Runtime Service/listener names are control-plane details.  Status clients
    # receive only publication data plus non-sensitive qualification signals.
    return {
        "id": node.node_id,
        "name": node.name,
        **asdict(node.publication),
        "region": node.runtime.region,
        "healthy": node.runtime.healthy,
        "qualifiedMbps": node.runtime.qualified_mbps,
        "qualified": node.runtime.healthy and node.runtime.qualified_mbps >= 100,
    }


def canonical_subject(user: str | None, email: str | None, aliases: dict[str, str]) -> str:
    normalized_aliases = {str(key).strip().casefold(): str(value).strip() for key, value in aliases.items()}
    for candidate in (email, user):
        value = str(candidate or "").strip()
        if value and value.casefold() in normalized_aliases:
            return normalized_aliases[value.casefold()]
    subject = str(user or email or "").strip()
    if not subject:
        raise ValueError("Dex identity required")
    return subject.casefold()


def authorization(cache: dict, subject: str, now: int | None = None) -> dict:
    current = int(time.time() if now is None else now)
    last_success = int(cache.get("lastSuccess", 0) or 0)
    if not last_success or current - last_success > AUTH_MAX_STALE_SECONDS:
        raise PermissionError("authorization cache stale")
    member = cache.get("members", {}).get(subject)
    if not member or not member.get("authorized"):
        raise PermissionError("GitHub repository access required")
    return member


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_token(secret: bytes, subject: str, device: str, ttl_days: int = 90, now: int | None = None) -> str:
    issued = int(now if now is not None else time.time())
    payload = {"sub": subject, "device": device, "iat": issued, "exp": issued + ttl_days * 86400,
               "jti": hashlib.sha256(f"{subject}\0{device}\0{issued}\0{secrets.token_hex(8)}".encode()).hexdigest()[:24]}
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
    query = urlencode({"sni": node.publication.sni, "insecure": "0"})
    if node.protocol == "anytls":
        return f"anytls://{password}@{node.server}:{node.port}/?{query}#{label}"
    if node.protocol == "hysteria2":
        return f"hysteria2://{password}@{node.server}:{node.port}/?{query}#{label}"
    raise ValueError(f"unsupported protocol: {node.protocol}")


def subscription_body(password: str, nodes: tuple[Node, ...] = DEFAULT_NODES) -> bytes:
    raw = "\n".join(node_uri(node, password) for node in published_nodes(nodes)) + "\n"
    return base64.b64encode(raw.encode())


def mihomo_proxy(node: Node, password: str) -> list[str]:
    p = node.publication
    credential_field = "password"
    return [f"  - name: {json.dumps(node.name, ensure_ascii=False)}", f"    type: {p.protocol}",
            f"    server: {p.server}", f"    port: {p.port}", f"    {credential_field}: {json.dumps(password)}",
            f"    sni: {p.sni}", "    skip-cert-verify: false", "    udp: true"]


def mihomo_config(password: str, token: str, base_url: str, nodes: tuple[Node, ...] = DEFAULT_NODES) -> bytes:
    active = published_nodes(nodes)
    names = [json.dumps(node.name, ensure_ascii=False) for node in active]
    out = ["mixed-port: 7890", "allow-lan: false", "mode: rule", "log-level: warning", "ipv6: false",
           "unified-delay: true", "tcp-concurrent: true", "proxies:"]
    for node in active:
        out.extend(mihomo_proxy(node, password))
    out.extend(["proxy-groups:", "  - name: PROXY", "    type: select", "    proxies:"])
    out.extend(f"      - {name}" for name in names)
    out.extend(["      - DIRECT", "  - name: AUTO", "    type: url-test", "    url: https://www.gstatic.com/generate_204",
                "    interval: 600", "    tolerance: 80", "    proxies:"])
    out.extend(f"      - {name}" for name in names)
    for _rule_id, label in SERVICE_RULESETS:
        out.extend([f"  - name: {label}", "    type: select", "    proxies:", "      - PROXY", "      - AUTO", "      - DIRECT"])
    rule_names = ("private", "cn", "geolocation-!cn", "cn-ip", "private-ip", "openai", "anthropic", "google",
                  "youtube", "telegram", "netflix", "disney", "hbo", "spotify", "tiktok", "twitter",
                  "github", "gitlab", "apple", "microsoft", "steam", "category-games")
    out.append("rule-providers:")
    for name in rule_names:
        behavior = "ipcidr" if name.endswith("-ip") else "domain"
        out.extend([f"  {name}:", "    type: http", f"    behavior: {behavior}", "    format: mrs",
                    f"    url: {base_url}/r/{token}/current/{name}.mrs", f"    path: ./rules/{name}.mrs", "    interval: 86400"])
    out.extend(["dns:", "  enable: true", "  ipv6: false", "  enhanced-mode: fake-ip", "  fake-ip-range: 192.0.2.99/16",
                "  default-nameserver:", "    - 192.0.2.58", "    - 192.0.2.95",
                "  proxy-server-nameserver:", "    - https://dns.alidns.com/dns-query", "    - https://doh.pub/dns-query",
                "  direct-nameserver:", "    - https://dns.alidns.com/dns-query", "    - https://doh.pub/dns-query",
                "  nameserver:", "    - https://1.1.1.1/dns-query#PROXY", "    - https://dns.google/dns-query#PROXY",
                "  nameserver-policy:", "    'rule-set:private,cn,apple,microsoft':", "      - https://dns.alidns.com/dns-query", "      - https://doh.pub/dns-query",
                "rules:", "  - RULE-SET,private,DIRECT", "  - RULE-SET,private-ip,DIRECT,no-resolve",
                "  - RULE-SET,apple,🍎 Apple", "  - RULE-SET,microsoft,🪟 Microsoft", "  - RULE-SET,openai,🤖 AI",
                "  - RULE-SET,anthropic,🤖 AI", "  - RULE-SET,google,🔎 Google / YouTube", "  - RULE-SET,youtube,🔎 Google / YouTube",
                "  - RULE-SET,telegram,✈️ Telegram", "  - RULE-SET,netflix,🎬 Streaming", "  - RULE-SET,disney,🎬 Streaming",
                "  - RULE-SET,hbo,🎬 Streaming", "  - RULE-SET,spotify,🎬 Streaming", "  - RULE-SET,tiktok,💬 Social",
                "  - RULE-SET,twitter,💬 Social", "  - RULE-SET,github,🧑‍💻 Developer", "  - RULE-SET,gitlab,🧑‍💻 Developer",
                "  - RULE-SET,steam,🎮 Games", "  - RULE-SET,category-games,🎮 Games", "  - RULE-SET,cn,DIRECT", "  - RULE-SET,cn-ip,DIRECT,no-resolve",
                "  - RULE-SET,geolocation-!cn,PROXY", "  - MATCH,PROXY", ""])
    return "\n".join(out).encode()


def shadowrocket_proxy(node: Node, password: str) -> str:
    publication = node.publication
    common = f"{node.name} = {publication.protocol}, {publication.server}, {publication.port}"
    if publication.protocol == "anytls":
        return f"{common}, password={password}, peer={publication.sni}, udp=1"
    if publication.protocol == "hysteria2":
        return f"{common}, auth={password}, peer={publication.sni}, udp=1"
    raise ValueError(f"unsupported Shadowrocket protocol: {publication.protocol}")


def shadowrocket_config(password: str, token: str, base_url: str,
                        nodes: tuple[Node, ...] = DEFAULT_NODES) -> bytes:
    active = published_nodes(nodes)
    names = [node.name for node in active]
    lines = ["[General]", "bypass-system = true", "ipv6 = false",
             "dns-server = https://dns.alidns.com/dns-query, https://doh.pub/dns-query",
             "udp-policy-not-supported-behaviour = REJECT", "", "[Proxy]"]
    lines.extend(shadowrocket_proxy(node, password) for node in active)
    lines.extend(["", "[Proxy Group]",
                  f"PROXY = select, AUTO, {', '.join(names)}",
                  f"AUTO = url-test, {', '.join(names)}, interval=600, tolerance=80, timeout=5, url=https://www.gstatic.com/generate_204"])
    for _rule_id, label in SERVICE_RULESETS:
        lines.append(f"{label} = select, PROXY, AUTO, DIRECT")
    mapping = (("private", "DIRECT"), ("apple", "🍎 Apple"), ("microsoft", "🪟 Microsoft"), ("ai", "🤖 AI"),
               ("google", "🔎 Google / YouTube"), ("telegram", "✈️ Telegram"), ("streaming", "🎬 Streaming"),
               ("social", "💬 Social"), ("developer", "🧑‍💻 Developer"), ("games", "🎮 Games"),
               ("cn", "DIRECT"), ("cn-ip", "DIRECT"))
    lines.extend(["", "[Rule]"])
    for rule_id, policy in mapping:
        lines.append(f"RULE-SET,{base_url}/r/{token}/current/{rule_id}.list,{policy}")
    lines.extend(["GEOIP,CN,DIRECT", "FINAL,PROXY", ""])
    return "\n".join(lines).encode()


def shadowrocket_import_url(config_url: str) -> str:
    return "shadowrocket://config/add/" + quote(config_url, safe="")


def month_end_epoch(now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    boundary = datetime(current.year + (current.month == 12), 1 if current.month == 12 else current.month + 1, 1, tzinfo=timezone.utc)
    return int(boundary.timestamp())


def subscription_headers(upload: int = 0, download: int = 0, now: datetime | None = None) -> dict[str, str]:
    return {"Subscription-Userinfo": f"upload={max(0, upload)}; download={max(0, download)}; total={MONTHLY_SOFT_LIMIT}; expire={month_end_epoch(now)}",
            "Profile-Title": PROFILE_TITLE, "Profile-Update-Interval": "6"}
