import importlib.util
import os
import pathlib
import struct
import unittest
from unittest import mock


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
                "enabled": True, "draining": False, "region": "test", "gatewayVIP": "192.0.2.213",
                "endpoint": {"type": "PublicIP", "value": "192.0.2.249"},
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

    def test_explicit_statuses_reject_redirect_loop(self):
        authority.SERVICE_DEFINITIONS = {
            "dex.re8ch.com.": {
                "service": "dex",
                "class": "web",
                "probePath": "/.well-known/openid-configuration",
                "acceptedStatuses": [200],
            }
        }
        self.assertTrue(authority.accepted_probe_status("dex", 200))
        self.assertFalse(authority.accepted_probe_status("dex", 308))

    def test_cn_prefers_high_capacity_r640(self):
        original_area = authority.AREA
        authority.AREA = "CN"
        authority.CANDIDATES[:] = [
            {"id": "small-cn", "region": "guangzhou", "area": "CN", "ip": "192.0.2.213", "capacityMbps": 100, "priority": 0, "probes": {"app": "https://app.re8ch.com/"}},
            {"id": "restricted-node-6dc3f140f9", "region": "loudi", "area": "CN", "ip": "192.0.2.219", "capacityMbps": 1000, "priority": 0, "probes": {"app": "https://app.re8ch.com/"}},
        ]
        authority.HEALTH["app"] = {name: {"ready": True, "latencyMs": 10} for name in ("small-cn", "restricted-node-6dc3f140f9")}
        try:
            self.assertEqual(authority.ranked("app")[0]["id"], "restricted-node-6dc3f140f9")
        finally:
            authority.AREA = original_area

    def test_us_prefers_regional_relay_over_remote_capacity(self):
        original_area = authority.AREA
        authority.AREA = "US"
        authority.CANDIDATES[:] = [
            {"id": "us-relay", "region": "los-angeles", "area": "US", "ip": "192.0.2.158", "capacityMbps": 100, "priority": 0, "forwarding": {"mode": "RegionalRelay", "originArea": "CN"}, "probes": {"app": "https://app.re8ch.com/"}},
            {"id": "restricted-node-6dc3f140f9", "region": "loudi", "area": "CN", "ip": "192.0.2.219", "capacityMbps": 1000, "priority": 0, "probes": {"app": "https://app.re8ch.com/"}},
        ]
        authority.HEALTH["app"] = {name: {"ready": True, "latencyMs": 10} for name in ("us-relay", "restricted-node-6dc3f140f9")}
        try:
            self.assertEqual(authority.ranked("app")[0]["id"], "us-relay")
        finally:
            authority.AREA = original_area

    def test_publisher_refreshes_per_service_edge_status(self):
        authority.CANDIDATES[:] = [{
            "id": "edge-a", "region": "test", "area": "test", "ip": "192.0.2.249",
            "probes": {"app": "https://app.re8ch.com/healthz"},
        }]
        authority.HEALTH = {"app": {"edge-a": {
            "ready": False, "statusCode": 404, "latencyMs": 9,
            "observedAt": 123, "failure": "unexpected status",
        }}}
        with mock.patch.object(authority, "NODE", "publisher"), \
             mock.patch.object(authority, "PUBLISHER_NODE", "publisher"), \
             mock.patch.object(authority, "KUBERNETES_API", "kubernetes"), \
             mock.patch.object(authority, "kubernetes_patch") as patch:
            authority.publish_edge_statuses()
        path, payload = patch.call_args.args
        self.assertEqual(path, "/apis/networking.re8ch.com/v1alpha1/publicedges/edge-a/status")
        self.assertEqual(payload["status"]["conditions"][0]["status"], "False")
        self.assertEqual(payload["status"]["services"]["app"]["statusCode"], 404)


if __name__ == "__main__":
    unittest.main()
