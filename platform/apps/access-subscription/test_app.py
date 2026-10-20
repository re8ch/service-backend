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
        from app import PORTAL, edge_access_nodes


class EdgeAccessDiscoveryTests(unittest.TestCase):
    def test_only_ready_qualified_public_edge_is_published(self):
        nodes = [
            {"metadata": {"name": "overseas-flk", "labels": {"networking.re8ch.com/edge-access-anytls": "enabled",
                           "topology.kubernetes.io/region": "de-fsn"},
                          "annotations": {"networking.re8ch.com/capacity-mbps": "400"}},
             "status": {"addresses": [{"type": "ExternalIP", "address": "2.28.106.230"}],
                        "conditions": [{"type": "Ready", "status": "True"}]}},
            {"metadata": {"name": "overseas-la-2", "labels": {"networking.re8ch.com/edge-access-anytls": "enabled"},
                          "annotations": {"networking.re8ch.com/capacity-mbps": "1000"}},
             "status": {"addresses": [{"type": "ExternalIP", "address": "179.253.231.159"}],
                        "conditions": [{"type": "Ready", "status": "False"}]}},
        ]
        pods = [{"metadata": {"labels": {"app.kubernetes.io/name": "edge-access-anytls"}},
                 "spec": {"nodeName": name}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}}
                for name in ("overseas-flk", "overseas-la-2")]
        active = edge_access_nodes(nodes, pods)
        self.assertEqual([node.node_id for node in active], ["edge-access-overseas-flk"])
        self.assertEqual(active[0].name, "🇩🇪 德国 Falkenstein · EdgeAccess")
        raw = base64.b64decode(subscription_body("device-password", active)).decode()
        self.assertIn("anytls://device-password@2.28.106.230:24443", raw)
        self.assertIn("sni=access.re8ch.com", raw)
        self.assertNotIn("179.253.231.159", raw)

    def test_live_region_labels_name_both_publications(self):
        nodes = [
            {"metadata": {"name": name, "labels": {"networking.re8ch.com/edge-access-anytls": "enabled",
                         "topology.kubernetes.io/region": region},
                          "annotations": {"networking.re8ch.com/capacity-mbps": "400"}},
             "status": {"addresses": [{"type": "ExternalIP", "address": address}],
                        "conditions": [{"type": "Ready", "status": "True"}]}}
            for name, region, address in (("overseas-la-2", "us-ca", "179.253.231.159"),
                                          ("overseas-flk", "de-fsn", "2.28.106.230"))]
        pods = [{"metadata": {"labels": {"app.kubernetes.io/name": "edge-access-anytls"}},
                 "spec": {"nodeName": name}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}}
                for name in ("overseas-flk", "overseas-la-2")]
        active = edge_access_nodes(nodes, pods)
        self.assertEqual({node.node_id: node.name for node in active}, {
            "edge-access-overseas-la-2": "🇺🇸 洛杉矶 2 · EdgeAccess",
            "edge-access-overseas-flk": "🇩🇪 德国 Falkenstein · EdgeAccess",
        })
        self.assertIn("v2rayN 的 Mihomo 核心", PORTAL)
        self.assertIn("${esc(j.mihomoURL)}</code>", PORTAL)


if __name__ == "__main__":
    unittest.main()
