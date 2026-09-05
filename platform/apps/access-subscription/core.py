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
SHADOWROCKET_PROFILE_REVISION = "20260905-cn-cloud-direct-1"

# Keep critical mainland cloud consoles deterministic even when an upstream
# community ruleset is incomplete or temporarily unavailable.
CN_CLOUD_DIRECT_DOMAINS = (
    "cloud.tencent.com",
    "tencentcloud.com",
    "tencentcloudapi.com",
    "qcloud.com",
    "myqcloud.com",
    "qcloudcdn.com",
    "aliyun.com",
    "aliyuncs.com",
    "alicdn.com",
    "huaweicloud.com",
    "huaweicloudapis.com",
    "myhuaweicloud.com",
)


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
    Node("backup", "🛟 Backup · Hysteria2", Publication("hysteria2", "backup.access.re8ch.com", 443, "backup.access.re8ch.com"), RuntimeTarget("personal-access-hysteria2", "access-hysteria2", "auto", 100, healthy=False)),
    # The first three entries above are immutable compatibility publications.
    # New topology-aware publications use distinct IDs and DNS names so an
    # existing Shadowrocket profile can refresh without losing a selected node.
    Node("route-auto-anytls", "♻️ RouteOS · Auto 决策 · AnyTLS", Publication("anytls", "access.re8ch.com", 443, "access.re8ch.com"), RuntimeTarget("personal-access-auto", "access-anytls", "routeos-auto", 100)),
    Node("route-auto-hysteria2", "♻️ RouteOS · Auto 决策 · Hysteria2", Publication("hysteria2", "backup.access.re8ch.com", 443, "backup.access.re8ch.com"), RuntimeTarget("personal-access-hysteria2", "access-hysteria2", "routeos-auto", 100, healthy=False)),
    Node("route-la-anytls", "🇺🇸 RouteOS · 洛杉矶 · AnyTLS", Publication("anytls", "route-la.access.re8ch.com", 443, "route-la.access.re8ch.com"), RuntimeTarget("personal-access-us-la", "access-anytls", "us-la", 100)),
    Node("route-la-hysteria2", "🇺🇸 RouteOS · 洛杉矶 · Hysteria2", Publication("hysteria2", "route-la.access.re8ch.com", 4443, "route-la.access.re8ch.com"), RuntimeTarget("personal-access-us-la", "access-hysteria2", "us-la", 100, healthy=False)),
    Node("route-kl-anytls", "🇲🇾 RouteOS · 吉隆坡 · AnyTLS", Publication("anytls", "my-kul.access.re8ch.com", 443, "my-kul.access.re8ch.com"), RuntimeTarget("personal-access-my-kul", "access-anytls", "my-kul", 100, healthy=False)),
    Node("route-kl-hysteria2", "🇲🇾 RouteOS · 吉隆坡 · Hysteria2", Publication("hysteria2", "route-kl.access.re8ch.com", 4444, "route-kl.access.re8ch.com"), RuntimeTarget("personal-access-my-kul", "access-hysteria2", "my-kul", 100, healthy=False)),
    Node("direct-la-anytls", "🇺🇸 Edge Direct · 洛杉矶 · AnyTLS", Publication("anytls", "direct-la.access.re8ch.com", 24443, "direct-la.access.re8ch.com"), RuntimeTarget("personal-access-us-la", "access-anytls", "us-la-direct", 100)),
    Node("direct-la-hysteria2", "🇺🇸 Edge Direct · 洛杉矶 · Hysteria2", Publication("hysteria2", "direct-la.access.re8ch.com", 24444, "direct-la.access.re8ch.com"), RuntimeTarget("personal-access-us-la", "access-hysteria2", "us-la-direct", 100)),
    Node("direct-ca-hysteria2", "🇺🇸 Edge Direct · 洛杉矶 2 · Hysteria2", Publication("hysteria2", "direct-ca.access.re8ch.com", 24444, "direct-ca.access.re8ch.com"), RuntimeTarget("personal-access-us-ca", "access-hysteria2", "us-ca-direct", 100)),
    Node("direct-kl-anytls", "🇲🇾 Edge Direct · 吉隆坡 · AnyTLS", Publication("anytls", "direct-kl.access.re8ch.com", 24443, "direct-kl.access.re8ch.com"), RuntimeTarget("personal-access-my-kul", "access-anytls", "my-kul-direct", 100)),
    Node("direct-kl-hysteria2", "🇲🇾 Edge Direct · 吉隆坡 · Hysteria2", Publication("hysteria2", "direct-kl.access.re8ch.com", 24444, "direct-kl.access.re8ch.com"), RuntimeTarget("personal-access-my-kul", "access-hysteria2", "my-kul-direct", 100)),
    Node("direct-cn-anytls", "🇨🇳 Edge Direct · 娄底 · AnyTLS", Publication("anytls", "direct-cn.access.re8ch.com", 24443, "direct-cn.access.re8ch.com"), RuntimeTarget("personal-access-cn-loudi", "access-anytls", "cn-loudi-direct", 100)),
    Node("direct-cn-hysteria2", "🇨🇳 Edge Direct · 娄底 · Hysteria2", Publication("hysteria2", "direct-cn.access.re8ch.com", 24444, "direct-cn.access.re8ch.com"), RuntimeTarget("personal-access-cn-loudi", "access-hysteria2", "cn-loudi-direct", 100)),
)

SERVICE_RULESETS = (
    ("ai", "🤖 AI"), ("google", "🔎 Google / YouTube"), ("telegram", "✈️ Telegram"),
    ("streaming", "🎬 Streaming"), ("social", "💬 Social"), ("developer", "🧑‍💻 Developer"),
    ("cloud", "☁️ Cloud Storage"), ("speedtest", "📶 Speed Test"),
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


def v2box_subscription_body(password: str, nodes: tuple[Node, ...] = DEFAULT_NODES) -> bytes:
    """Return only protocols V2Box can import from this service."""
    compatible = tuple(node for node in published_nodes(nodes) if node.protocol == "hysteria2")
    raw = "\n".join(node_uri(node, password) for node in compatible) + "\n"
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
    stable_ids = ("direct-kl-anytls", "direct-la-anytls", "route-kl-anytls", "route-la-anytls", "us-ca")
    stable = [node for node_id in stable_ids for node in active if node.node_id == node_id]
    stable_names = [json.dumps(node.name, ensure_ascii=False) for node in stable]
    out = ["mixed-port: 7890", "allow-lan: false", "mode: rule", "log-level: warning", "ipv6: false",
           "unified-delay: true", "tcp-concurrent: true", "proxies:"]
    for node in active:
        out.extend(mihomo_proxy(node, password))
    out.extend(["proxy-groups:", "  - name: PROXY", "    type: select", "    proxies:", "      - CODEX-STABLE", "      - AUTO"])
    out.extend(f"      - {name}" for name in names)
    out.extend(["      - DIRECT", "  - name: CODEX-STABLE", "    type: select", "    proxies:"])
    out.extend(f"      - {name}" for name in stable_names)
    out.extend(["      - DIRECT", "  - name: AUTO", "    type: url-test", "    url: https://www.gstatic.com/generate_204",
                "    interval: 600", "    tolerance: 80", "    proxies:"])
    out.extend(f"      - {name}" for name in stable_names)
    for _rule_id, label in SERVICE_RULESETS:
        if _rule_id == "ai":
            choices = ("CODEX-STABLE", "PROXY", "AUTO", "DIRECT")
        else:
            choices = ("DIRECT", "AUTO", "PROXY") if _rule_id in {"apple", "microsoft", "speedtest"} else ("PROXY", "AUTO", "DIRECT")
        out.extend([f"  - name: {label}", "    type: select", "    proxies:"])
        out.extend(f"      - {choice}" for choice in choices)
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
                "  nameserver-policy:",
                *[line for domain in CN_CLOUD_DIRECT_DOMAINS for line in
                  (f"    '+.{domain}':", "      - https://dns.alidns.com/dns-query", "      - https://doh.pub/dns-query")],
                "    'rule-set:private,cn,apple,microsoft':", "      - https://dns.alidns.com/dns-query", "      - https://doh.pub/dns-query",
                "rules:", "  - RULE-SET,private,DIRECT", "  - RULE-SET,private-ip,DIRECT,no-resolve",
                *[f"  - DOMAIN-SUFFIX,{domain},DIRECT" for domain in CN_CLOUD_DIRECT_DOMAINS],
                "  - RULE-SET,apple,🍎 Apple", "  - RULE-SET,microsoft,🪟 Microsoft", "  - RULE-SET,openai,🤖 AI",
                "  - RULE-SET,anthropic,🤖 AI", "  - RULE-SET,google,🔎 Google / YouTube", "  - RULE-SET,youtube,🔎 Google / YouTube",
                "  - RULE-SET,telegram,✈️ Telegram", "  - RULE-SET,netflix,🎬 Streaming", "  - RULE-SET,disney,🎬 Streaming",
                "  - RULE-SET,hbo,🎬 Streaming", "  - RULE-SET,spotify,🎬 Streaming", "  - RULE-SET,tiktok,💬 Social",
                "  - RULE-SET,twitter,💬 Social", "  - RULE-SET,github,🧑‍💻 Developer", "  - RULE-SET,gitlab,🧑‍💻 Developer",
                "  - RULE-SET,steam,🎮 Games", "  - RULE-SET,category-games,🎮 Games", "  - DOMAIN-SUFFIX,cn,DIRECT", "  - RULE-SET,cn,DIRECT", "  - RULE-SET,cn-ip,DIRECT,no-resolve",
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
    stable_ids = ("direct-kl-anytls", "direct-la-anytls", "route-kl-anytls", "route-la-anytls", "us-ca")
    stable_names = [node.name for node_id in stable_ids for node in active if node.node_id == node_id]
    config_url = f"{base_url}/s/{token}/shadowrocket.conf?v={SHADOWROCKET_PROFILE_REVISION}"
    lines = ["[General]", "bypass-system = true", "ipv6 = false", "prefer-ipv6 = false",
             "skip-proxy = 192.0.2.136/16, 192.0.2.183/8, 192.0.2.33/12, 127.0.0.0/8, localhost, *.local",
             "tun-excluded-routes = 192.0.2.183/8, 192.0.2.81/10, 127.0.0.0/8, 192.0.2.95/16, 192.0.2.33/12, 192.0.2.136/16, 224.0.0.0/4",
             "dns-server = https://1.1.1.1/dns-query, https://dns.google/dns-query",
             "fallback-dns-server = https://dns.alidns.com/dns-query, https://doh.pub/dns-query",
             "dns-direct-system = false", "dns-fallback-system = false",
             "icmp-auto-reply = true", "private-ip-answer = false",
             f"update-url = {config_url}",
             "udp-policy-not-supported-behaviour = REJECT", "", "[Proxy]"]
    lines.extend(shadowrocket_proxy(node, password) for node in active)
    lines.extend(["", "[Proxy Group]",
                  f"PROXY = select, CODEX-STABLE, AUTO, {', '.join(names)}",
                  f"CODEX-STABLE = select, {', '.join(stable_names)}",
                  f"AUTO = url-test, {', '.join(stable_names)}, interval=600, tolerance=80, timeout=5, url=https://www.gstatic.com/generate_204"])
    for _rule_id, label in SERVICE_RULESETS:
        if _rule_id == "ai":
            choices = "CODEX-STABLE, PROXY, AUTO, DIRECT"
        else:
            choices = "DIRECT, AUTO, PROXY" if _rule_id in {"apple", "microsoft", "speedtest"} else "PROXY, AUTO, DIRECT"
        lines.append(f"{label} = select, {choices}")
    lines.append("🛡️ Ad Blocking = select, REJECT, DIRECT")
    mapping = (("private", "DIRECT", ""), ("adblock", "🛡️ Ad Blocking", ""),
               ("direct-services", "DIRECT", ""), ("apple", "🍎 Apple", ""),
               ("microsoft", "🪟 Microsoft", ""), ("ai", "🤖 AI", ""),
               ("google", "🔎 Google / YouTube", ""), ("telegram", "✈️ Telegram", ""),
               ("streaming", "🎬 Streaming", ""), ("social", "💬 Social", ""),
               ("developer", "🧑‍💻 Developer", ""), ("cloud", "☁️ Cloud Storage", ""),
               ("speedtest", "📶 Speed Test", ""), ("games", "🎮 Games", ""),
               ("cn", "DIRECT", ""), ("cn-ip", "DIRECT", ",no-resolve"))
    lines.extend(["", "[Rule]", "DOMAIN,auth.openai.com,🤖 AI", "DOMAIN-SUFFIX,openai.com,🤖 AI", "DOMAIN-SUFFIX,chatgpt.com,🤖 AI"])
    lines.extend(f"DOMAIN-SUFFIX,{domain},DIRECT" for domain in CN_CLOUD_DIRECT_DOMAINS)
    for rule_id, policy, option in mapping:
        lines.append(f"RULE-SET,{base_url}/r/{token}/current/{rule_id}.list,{policy}{option}")
    lines.extend(["DOMAIN-SUFFIX,cn,DIRECT", "GEOIP,CN,DIRECT,no-resolve", "FINAL,PROXY", ""])
    return "\n".join(lines).encode()


def shadowrocket_import_url(config_url: str) -> str:
    # Shadowrocket's config installer takes the percent-encoded remote URL as
    # the path payload. The query form may open the app without adding a file.
    return "shadowrocket://config/add/" + quote(config_url, safe="")


def v2box_subscription_import_url(subscription_url: str, name: str = PROFILE_TITLE) -> str:
    return "v2box://install-sub?" + urlencode({"url": subscription_url, "name": name})


def v2box_config_import_url(config_url: str) -> str:
    return "v2box://install-config?" + urlencode({"url": config_url})


def clash_meta_import_url(config_url: str) -> str:
    """Build the URL scheme understood by ClashX and Clash Meta clients."""
    return "clash://install-config?url=" + quote(config_url, safe="")


def month_end_epoch(now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    boundary = datetime(current.year + (current.month == 12), 1 if current.month == 12 else current.month + 1, 1, tzinfo=timezone.utc)
    return int(boundary.timestamp())


def subscription_headers(upload: int = 0, download: int = 0, now: datetime | None = None) -> dict[str, str]:
    return {"Subscription-Userinfo": f"upload={max(0, upload)}; download={max(0, download)}; total={MONTHLY_SOFT_LIMIT}; expire={month_end_epoch(now)}",
            "Profile-Title": PROFILE_TITLE, "Profile-Update-Interval": "6",
            "Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
