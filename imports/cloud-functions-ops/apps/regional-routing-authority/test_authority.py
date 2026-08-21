import importlib.util
import os
import pathlib
import struct
import unittest


os.environ.setdefault("CANDIDATES_JSON", "[]")
MODULE_PATH = pathlib.Path(__file__).with_name("authority.py")
SPEC = importlib.util.spec_from_file_location("regional_authority", MODULE_PATH)
authority = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(authority)


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        authority.SERVICE_DEFINITIONS = {
            "app.re8ch.com.": {"service": "app", "class": "web", "probePath": "/healthz"}
        }
        authority.SERVICES = {"app.re8ch.com.": "app"}
        authority.HEALTH = {"app": {}}
        authority.CANDIDATES[:] = []

    def test_disabled_and_draining_edges_are_not_candidates(self):
        payload = {"items": [
            {"metadata": {"name": "disabled"}, "spec": {"enabled": False, "draining": False}},
            {"metadata": {"name": "draining"}, "spec": {"enabled": True, "draining": True}},
        ]}
        original = authority.kubernetes_get
        authority.kubernetes_get = lambda _path: payload
        try:
            self.assertEqual(authority.candidates_from_public_edges(), [])
        finally:
            authority.kubernetes_get = original

    def test_public_edge_builds_service_probe(self):
        payload = {"items": [{
            "metadata": {"name": "edge-a"},
            "spec": {
                "enabled": True, "draining": False, "region": "test", "gatewayVIP": "192.0.2.209",
                "endpoint": {"type": "PublicIP", "value": "192.0.2.95"},
                "serviceClasses": ["web"],
            },
        }]}
        original = authority.kubernetes_get
        authority.kubernetes_get = lambda _path: payload
        try:
            candidates = authority.candidates_from_public_edges()
        finally:
            authority.kubernetes_get = original
        self.assertEqual(candidates[0]["probes"]["app"], "https://app.re8ch.com/healthz")

    def test_dns_fails_closed_without_ready_edge(self):
        query = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + authority.encode_name("app.re8ch.com.") + struct.pack("!HH", 1, 1)
        response = authority.dns_response(query)
        self.assertEqual(struct.unpack("!H", response[6:8])[0], 0)


if __name__ == "__main__":
    unittest.main()
