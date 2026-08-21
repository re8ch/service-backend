import base64
import json
import unittest
from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import Identity, MobileService, project_mobile_egresses


class MobileEgressTests(unittest.TestCase):
    def setUp(self):
        self.nodes = [
            {"name":"la","role":"spine","asn":65002,"region":"us-ca","uplinkMbps":1000},
            {"name":"leaf","role":"leaf","asn":4200000016,"region":"cn-guangzhou","uplinkMbps":3},
        ]
        self.status = [
            {"node":"la","timestamp":1,"isGlobalExit":True,"consumer":{"ok":True,"latencyMs":42}},
            {"node":"leaf","timestamp":1,"isCnExit":True,"consumer":{"ok":True,"latencyMs":8}},
        ]
        self.transport = [{"type":"socks5","host":"exit.example.invalid","port":1080}]

    def test_projection_is_explicit_and_rejects_ordinary_leaf(self):
        value = project_mobile_egresses(self.nodes, self.status, [
            {"id":"la","nodeName":"la","capability":"global","transports":self.transport},
            {"id":"leaf","nodeName":"leaf","capability":"cn","transports":self.transport},
        ])
        self.assertEqual([item["id"] for item in value], ["la"])

    def test_projection_removes_unhealthy_or_draining(self):
        gateways = [{"id":"la","nodeName":"la","capability":"global","transports":self.transport,"draining":True}]
        self.assertEqual(project_mobile_egresses(self.nodes, self.status, gateways), [])

    def service(self):
        catalog = project_mobile_egresses(self.nodes, self.status, [
            {"id":"la","nodeName":"la","capability":"global","transports":self.transport}
        ])
        return MobileService(lambda: catalog, Ed25519PrivateKey.generate(), b"x"*32,
                             now=lambda: datetime(2026, 8, 21, tzinfo=timezone.utc))

    def test_enrollment_is_stable_and_catalog_is_signed(self):
        service = self.service()
        first = service.enroll(Identity("user-1"), {"platform":"ios","devicePublicKey":"k"*64})
        second = service.enroll(Identity("user-1"), {"platform":"ios","devicePublicKey":"k"*64})
        self.assertEqual(first, second)
        envelope, etag = service.egresses()
        payload = json.loads(base64.b64decode(envelope["payload"]))
        self.assertEqual(payload["egresses"][0]["id"], "la")
        self.assertEqual(len(etag), 64)

    def test_credentials_are_scoped_and_unknown_exit_is_rejected(self):
        service = self.service()
        value = service.credentials(Identity("user-1"), "device", "la")
        self.assertEqual(value["egressID"], "la")
        with self.assertRaises(LookupError):
            service.credentials(Identity("user-1"), "device", "missing")

    def test_telemetry_rejects_full_urls_and_accepts_etld1(self):
        service = self.service()
        self.assertEqual(service.accept_telemetry("d", {"samples":[{"eTLDPlusOne":"github.com","bytes":1}]}), {"accepted":1})
        with self.assertRaises(ValueError):
            service.accept_telemetry("d", {"samples":[{"eTLDPlusOne":"github.com","url":"https://github.com/a"}]})


if __name__ == "__main__": unittest.main()
