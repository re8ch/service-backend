import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from secretary import cluster_watcher


class ClusterWatcherTest(unittest.TestCase):
    def test_incident_is_deterministic_and_requests_email(self):
        with patch.dict(os.environ, {"SECRETARY_WEBHOOK_SECRET": "test", "SECRETARY_NOTIFY_TO": "owner@example.com"}), \
             patch.object(cluster_watcher, "request_json", return_value={}) as request_json:
            cluster_watcher.incident("node/test", "2026-09-14T00:00:00Z", "high", "not ready", "node-test")
        payload = request_json.call_args.kwargs["body"]
        self.assertEqual(payload["notification_email"]["to"], "owner@example.com")
        self.assertTrue(payload["idempotency_key"].startswith("cluster-health:"))
        self.assertTrue(payload["incident_id"].startswith("inc_cluster_"))


if __name__ == "__main__":
    unittest.main()
