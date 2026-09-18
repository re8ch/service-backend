import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from secretary import cluster_watcher


class ClusterWatcherTest(unittest.TestCase):
    def test_observation_is_deterministic_and_does_not_request_delivery(self):
        with patch.dict(os.environ, {"SECRETARY_WEBHOOK_SECRET": "test"}), \
             patch.object(cluster_watcher, "request_json", return_value={}) as request_json:
            cluster_watcher.observe("node/test", "2026-09-14T00:00:00Z", "high", "not ready", "node-test")
        payload = request_json.call_args.kwargs["body"]
        self.assertNotIn("notification_email", payload)
        self.assertTrue(payload["idempotency_key"].startswith("cluster-health:"))
        self.assertTrue(payload["interaction_id"].startswith("cluster-health:"))


if __name__ == "__main__":
    unittest.main()
