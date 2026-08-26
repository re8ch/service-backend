import base64
import unittest
from datetime import datetime, timezone

from core import DEFAULT_NODES, MONTHLY_SOFT_LIMIT, issue_token, node_uri, proxy_password, subscription_body, subscription_headers, verify_token


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.secret = b"x" * 32

    def test_token_round_trip_and_expiry(self):
        token = issue_token(self.secret, "woomou", "iphone", now=100)
        self.assertEqual(verify_token(self.secret, token, now=101)["device"], "iphone")
        with self.assertRaises(ValueError):
            verify_token(self.secret, token, now=100 + 91 * 86400)

    def test_tampered_token_is_rejected(self):
        token = issue_token(self.secret, "woomou", "iphone", now=100)
        with self.assertRaises(ValueError):
            verify_token(self.secret, token + "x", now=101)

    def test_subscription_contains_real_nodes(self):
        raw = base64.b64decode(subscription_body("credential")).decode()
        self.assertEqual(len(raw.strip().splitlines()), 3)
        self.assertIn("anytls://credential@us-ca.access.re8ch.com:443", raw)
        self.assertIn("hysteria2://credential@backup.access.re8ch.com:443", raw)

    def test_uri_uses_secure_tls(self):
        uri = node_uri(DEFAULT_NODES[0], "credential")
        self.assertIn("insecure=0", uri)
        self.assertIn("sni=access.re8ch.com", uri)

    def test_soft_quota_headers(self):
        headers = subscription_headers(12, 34, datetime(2026, 8, 26, tzinfo=timezone.utc))
        self.assertEqual(headers["Profile-Title"], "access re8ch com")
        self.assertIn(f"total={MONTHLY_SOFT_LIMIT}", headers["Subscription-Userinfo"])
        self.assertIn("expire=1788220800", headers["Subscription-Userinfo"])

    def test_proxy_password_is_device_scoped(self):
        self.assertNotEqual(proxy_password(self.secret, "a"), proxy_password(self.secret, "b"))


if __name__ == "__main__":
    unittest.main()
