import base64
import unittest
from datetime import datetime, timezone

from core import (DEFAULT_NODES, MONTHLY_SOFT_LIMIT, authorization, canonical_subject, clash_meta_import_url, issue_token, mihomo_config,
                  node_uri, proxy_password, public_node, shadowrocket_config, shadowrocket_import_url,
                  subscription_body, subscription_headers, v2box_config_import_url,
                  v2box_subscription_body, v2box_subscription_import_url, verify_token)


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.secret = b"x" * 32

    def test_token_round_trip_and_expiry(self):
        token = issue_token(self.secret, "hliu74", "iphone", now=100)
        self.assertEqual(verify_token(self.secret, token, now=101)["device"], "iphone")
        with self.assertRaises(ValueError):
            verify_token(self.secret, token, now=100 + 91 * 86400)

    def test_tampered_token_is_rejected(self):
        token = issue_token(self.secret, "hliu74", "iphone", now=100)
        with self.assertRaises(ValueError):
            verify_token(self.secret, token + "x", now=101)

    def test_subscription_uses_publication_not_runtime_target(self):
        raw = base64.b64decode(subscription_body("credential")).decode()
        self.assertEqual(len(raw.strip().splitlines()), 11)
        self.assertIn("anytls://credential@us-ca.access.re8ch.com:443", raw)
        self.assertNotIn("personal-access-us-ca", raw)
        self.assertNotIn("route-la.access.re8ch.com:4443", raw)
        self.assertIn("direct-la.access.re8ch.com:24443", raw)
        self.assertIn("direct-cn.access.re8ch.com:24444", raw)
        status = public_node(DEFAULT_NODES[1])
        self.assertEqual(status["server"], "us-ca.access.re8ch.com")
        self.assertNotIn("runtime", status)
        self.assertNotIn("service", status)

    def test_uri_uses_secure_tls(self):
        uri = node_uri(DEFAULT_NODES[0], "credential")
        self.assertIn("insecure=0", uri)
        self.assertIn("sni=access.re8ch.com", uri)

    def test_soft_quota_headers(self):
        headers = subscription_headers(12, 34, datetime(2026, 8, 26, tzinfo=timezone.utc))
        self.assertEqual(headers["Profile-Title"], "access re8ch com")
        self.assertEqual(headers["Cache-Control"], "no-store, no-cache, must-revalidate")
        self.assertEqual(headers["Pragma"], "no-cache")
        self.assertIn(f"total={MONTHLY_SOFT_LIMIT}", headers["Subscription-Userinfo"])
        self.assertIn("expire=1788220800", headers["Subscription-Userinfo"])

    def test_proxy_password_is_device_scoped(self):
        self.assertNotEqual(proxy_password(self.secret, "a"), proxy_password(self.secret, "b"))

    def test_identity_aliases_merge_github_and_local_login(self):
        aliases = {"hliu74@apple.com": "hliu74", "admin@re8ch.com": "hliu74", "woomou": "hliu74"}
        self.assertEqual(canonical_subject("woomou", "hliu74@apple.com", aliases), "hliu74")
        self.assertEqual(canonical_subject("local-subject", "admin@re8ch.com", aliases), "hliu74")

    def test_authorization_cache_and_fail_closed(self):
        cache = {"lastSuccess": 100, "members": {"hliu74": {"authorized": True, "admin": True}}}
        self.assertTrue(authorization(cache, "hliu74", now=101)["admin"])
        with self.assertRaises(PermissionError):
            authorization(cache, "other", now=101)
        with self.assertRaises(PermissionError):
            authorization(cache, "hliu74", now=100 + 86401)

    def test_mihomo_config_has_split_dns_and_mrs(self):
        config = mihomo_config("credential", "token", "https://sub.access.re8ch.com").decode()
        self.assertIn("enhanced-mode: fake-ip", config)
        self.assertIn("nameserver-policy:", config)
        self.assertIn("format: mrs", config)
        self.assertIn("RULE-SET,cn-ip,DIRECT,no-resolve", config)
        self.assertIn("DOMAIN-SUFFIX,cn,DIRECT", config)
        self.assertIn("DOMAIN-SUFFIX,cloud.tencent.com,DIRECT", config)
        self.assertIn("'+.cloud.tencent.com':", config)
        self.assertLess(config.index("DOMAIN-SUFFIX,cloud.tencent.com,DIRECT"), config.index("MATCH,PROXY"))
        self.assertIn("name: CODEX-STABLE", config)
        self.assertNotIn("fallback:", config)

    def test_shadowrocket_config_defaults_to_proxy(self):
        config = shadowrocket_config("credential", "token", "https://sub.access.re8ch.com").decode()
        self.assertIn("[Proxy]", config)
        self.assertIn("♻️ Auto · Smart Select = anytls, access.re8ch.com, 443, password=credential, peer=access.re8ch.com, udp=1", config)
        self.assertNotIn("🛟 Backup · Hysteria2", config)
        self.assertIn("♻️ RouteOS · Auto 决策 · AnyTLS", config)
        self.assertNotIn("🇺🇸 RouteOS · 洛杉矶 · Hysteria2", config)
        self.assertIn("🇲🇾 Edge Direct · 吉隆坡 · AnyTLS = anytls, direct-kl.access.re8ch.com, 24443", config)
        self.assertIn("[Proxy Group]", config)
        self.assertIn("PROXY = select, CODEX-STABLE, AUTO", config)
        self.assertIn("CODEX-STABLE = select, 🇲🇾 Edge Direct · 吉隆坡 · AnyTLS", config)
        self.assertNotIn("AUTO = url-test, ♻️ Auto · Smart Select", config)
        self.assertIn("🤖 AI = select, CODEX-STABLE, PROXY, AUTO, DIRECT", config)
        self.assertIn("update-url = https://sub.access.re8ch.com/s/token/shadowrocket.conf?v=20260905-cn-cloud-direct-1", config)
        self.assertIn("skip-proxy = 192.0.2.148/16", config)
        self.assertIn("dns-server = https://1.1.1.1/dns-query, https://dns.google/dns-query", config)
        self.assertIn("fallback-dns-server = https://dns.alidns.com/dns-query, https://doh.pub/dns-query", config)
        self.assertIn("DOMAIN,auth.openai.com,🤖 AI", config)
        self.assertIn("🍎 Apple = select, DIRECT, AUTO, PROXY", config)
        self.assertIn("🛡️ Ad Blocking = select, REJECT, DIRECT", config)
        self.assertIn("/current/direct-services.list,DIRECT", config)
        self.assertIn("/current/cn-ip.list,DIRECT,no-resolve", config)
        self.assertIn("DOMAIN-SUFFIX,cn,DIRECT", config)
        self.assertIn("DOMAIN-SUFFIX,cloud.tencent.com,DIRECT", config)
        self.assertIn("DOMAIN-SUFFIX,qcloud.com,DIRECT", config)
        self.assertLess(config.index("DOMAIN-SUFFIX,cloud.tencent.com,DIRECT"), config.index("FINAL,PROXY"))
        self.assertIn("FINAL,PROXY", config)
        self.assertIn("/current/cn.list,DIRECT", config)
        self.assertNotIn("personal-access-us-ca", config)

    def test_shadowrocket_import_url_encodes_complete_config_endpoint(self):
        url = shadowrocket_import_url("https://sub.access.re8ch.com/s/a.b/shadowrocket.conf")
        self.assertEqual(url, "shadowrocket://config/add/https%3A%2F%2Fsub.access.re8ch.com%2Fs%2Fa.b%2Fshadowrocket.conf")

    def test_v2box_import_urls_encode_subscription_and_single_config(self):
        subscription = v2box_subscription_import_url("https://sub.access.re8ch.com/s/a.b/v2box.txt")
        self.assertEqual(subscription, "v2box://install-sub?url=https%3A%2F%2Fsub.access.re8ch.com%2Fs%2Fa.b%2Fv2box.txt&name=access+re8ch+com")
        config = v2box_config_import_url("anytls://secret@example.com:443/?sni=example.com")
        self.assertEqual(config, "v2box://install-config?url=anytls%3A%2F%2Fsecret%40example.com%3A443%2F%3Fsni%3Dexample.com")

    def test_v2box_subscription_contains_only_verified_hysteria2_nodes(self):
        raw = base64.b64decode(v2box_subscription_body("credential")).decode()
        self.assertEqual(len(raw.strip().splitlines()), 4)
        self.assertIn("hysteria2://credential@direct-la.access.re8ch.com:24444", raw)
        self.assertIn("hysteria2://credential@direct-ca.access.re8ch.com:24444", raw)
        self.assertIn("hysteria2://credential@direct-kl.access.re8ch.com:24444", raw)
        self.assertIn("hysteria2://credential@direct-cn.access.re8ch.com:24444", raw)
        self.assertNotIn("anytls://", raw)
        self.assertNotIn("route-la.access.re8ch.com:4443", raw)

    def test_clash_meta_import_url_encodes_complete_config_endpoint(self):
        url = clash_meta_import_url("https://sub.access.re8ch.com/s/a.b/mihomo.yaml")
        self.assertEqual(url, "clash://install-config?url=https%3A%2F%2Fsub.access.re8ch.com%2Fs%2Fa.b%2Fmihomo.yaml")


if __name__ == "__main__":
    unittest.main()
