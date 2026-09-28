import unittest
from unittest import mock

from mihomo_node_labels import desired_labels, reconcile


class MihomoNodeLabelsTests(unittest.TestCase):
    def test_ready_node_defaults_to_consumer_and_public_global_exit(self):
        inventory = {"overseas-flk": {"region": "de-fsn", "capacityMbps": 400}}
        node = {"metadata": {"name": "overseas-flk"},
                "status": {"addresses": [{"type": "ExternalIP", "address": "2.28.106.230"}],
                           "conditions": [{"type": "Ready", "status": "True"}]}}
        self.assertEqual(desired_labels(node, inventory), {
            "networking.re8ch.com/mihomo-node": "enabled",
            "networking.re8ch.com/geo": "global",
            "networking.re8ch.com/mihomo-exit": "enabled",
        })

    def test_unlisted_ready_node_uses_topology_and_private_node_stays_consumer(self):
        inventory = {"amdwsl-zt": {"region": "cn-hubei", "capacityMbps": 100},
                     "overseas-la-2": {"region": "us-ca", "capacityMbps": 1000}}
        private = {"metadata": {"name": "overseas-la-2"},
                   "status": {"addresses": [{"type": "ExternalIP", "address": "10.0.0.2"}],
                              "conditions": [{"type": "Ready", "status": "True"}]}}
        self.assertEqual(desired_labels(private, inventory), {
            "networking.re8ch.com/mihomo-node": "enabled", "networking.re8ch.com/geo": "global"})
        unlisted = {"metadata": {"name": "new-edge", "labels": {
            "topology.kubernetes.io/region": "us-east"}},
            "status": {"conditions": [{"type": "Ready", "status": "True"}],
                       "addresses": [{"type": "ExternalIP", "address": "8.8.8.8"}]}}
        self.assertEqual(desired_labels(unlisted, inventory), {
            "networking.re8ch.com/mihomo-node": "enabled",
            "networking.re8ch.com/geo": "global",
            "networking.re8ch.com/mihomo-exit": "enabled"})
        unlisted["status"]["conditions"][0]["status"] = "False"
        self.assertEqual(desired_labels(unlisted, inventory), {})

    def test_not_ready_node_loses_managed_labels(self):
        node = {"metadata": {"name": "old-edge", "labels": {
            "networking.re8ch.com/mihomo-node": "enabled",
            "networking.re8ch.com/geo": "global",
            "networking.re8ch.com/mihomo-exit": "enabled"}},
            "status": {"conditions": [{"type": "Ready", "status": "False"}]}}
        with mock.patch("mihomo_node_labels.api") as api:
            api.side_effect = [{"items": [node]}, {"data": {"nodes.json": "{}"}}, {}]
            reconcile()
        self.assertEqual(api.call_args_list[2].args[2], {
            "metadata": {"labels": {
                "networking.re8ch.com/mihomo-node": None,
                "networking.re8ch.com/geo": None,
                "networking.re8ch.com/mihomo-exit": None}}})


if __name__ == "__main__":
    unittest.main()
