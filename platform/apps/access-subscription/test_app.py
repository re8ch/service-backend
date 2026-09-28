"""Qualification checks for dynamically discovered EdgeAccess publications."""

import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import subscription_body


with tempfile.TemporaryDirectory() as directory:
    secret = Path(directory) / "hmac-key"
    secret.write_bytes(b"test-secret")
    with patch.dict(os.environ, {"HMAC_SECRET_PATH": str(secret)}):
        from app import edge_access_nodes


class EdgeAccessDiscoveryTests(unittest.TestCase):
    def test_only_ready_public_edge_with_tls_probe_is_published(self):
        nodes = [
            {"metadata": {"name": "overseas-flk", "labels": {"networking.re8ch.com/edge-access-anytls": "enabled",
                           "topology.kubernetes.io/region": "de-fsn"},
                          "annotations": {"networking.re8ch.com/capacity-mbps": "400"}},
             "status": {"addresses": [{"type": "ExternalIP", "address": "192.0.2.101"}],
                        "conditions": [{"type": "Ready", "status": "True"}]}},
            {"metadata": {"name": "overseas-la-2", "labels": {"networking.re8ch.com/edge-access-anytls": "enabled"},
                          "annotations": {"networking.re8ch.com/capacity-mbps": "1000"}},
             "status": {"addresses": [{"type": "ExternalIP", "address": "192.0.2.38"}],
                        "conditions": [{"type": "Ready", "status": "False"}]}},
        ]
        pods = [{"metadata": {"labels": {"app.kubernetes.io/name": "edge-access-anytls"}},
                 "spec": {"nodeName": name}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}}
                for name in ("overseas-flk", "overseas-la-2")]
        active = edge_access_nodes(nodes, pods, lambda ip: ip == "192.0.2.101")
        self.assertEqual([node.node_id for node in active], ["edge-access-overseas-flk"])
        raw = base64.b64decode(subscription_body("device-password", active)).decode()
        self.assertIn("anytls://device-password@192.0.2.101:24443", raw)
        self.assertIn("sni=access.re8ch.com", raw)
        self.assertNotIn("192.0.2.38", raw)


if __name__ == "__main__":
    unittest.main()
