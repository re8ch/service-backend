import unittest

from mihomo_node_labels import desired_labels


class MihomoNodeLabelsTests(unittest.TestCase):
    def test_active_inventory_defaults_to_consumer_and_public_global_exit(self):
        inventory = {"overseas-flk": {"region": "de-fsn", "capacityMbps": 400}}
        node = {"metadata": {"name": "overseas-flk"},
                "status": {"addresses": [{"type": "ExternalIP", "address": "192.0.2.38"}]}}
        self.assertEqual(desired_labels(node, inventory), {
            "networking.re8ch.com/mihomo-node": "enabled",
            "networking.re8ch.com/geo": "global",
            "networking.re8ch.com/mihomo-exit": "enabled",
        })

    def test_private_and_unlisted_nodes_do_not_become_global_exits(self):
        inventory = {"amdwsl-zt": {"region": "cn-hubei", "capacityMbps": 100},
                     "overseas-la-2": {"region": "us-ca", "capacityMbps": 1000}}
        private = {"metadata": {"name": "overseas-la-2"},
                   "status": {"addresses": [{"type": "ExternalIP", "address": "192.0.2.132"}]}}
        self.assertEqual(desired_labels(private, inventory), {
            "networking.re8ch.com/mihomo-node": "enabled", "networking.re8ch.com/geo": "global"})
        self.assertEqual(desired_labels({"metadata": {"name": "amdwsl-zt"}}, inventory), {
            "networking.re8ch.com/mihomo-node": "enabled", "networking.re8ch.com/geo": "cn"})
        self.assertEqual(desired_labels({"metadata": {"name": "unknown"}}, inventory), {})


if __name__ == "__main__":
    unittest.main()
