#!/usr/bin/env python3
import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("advisor", ROOT / "operations/scripts/re8ch-routing-advisor.py")
advisor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(advisor)


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = json.loads((ROOT / "clusters/re8ch/components/network/public-mesh/node-costs.yaml").read_text())
        cls.paths = json.loads((ROOT / "clusters/re8ch/components/network/public-mesh/network-costs.yaml").read_text())

    def test_catalogs_are_valid_and_cover_live_inventory(self):
        advisor.validate_catalogs(self.nodes, self.paths)
        names = {node["name"] for node in self.nodes["nodes"]}
        self.assertEqual(12, len(names))
        self.assertIn("pg-re8ch-replica-186", names)
        self.assertEqual(11, len(names - {"pg-re8ch-replica-186"}))

    def test_unknown_prices_are_not_zero(self):
        entry = advisor.path_entry(self.paths, "r640", "rancher-panel", "public_wg")
        self.assertIsNone(advisor.tiered_cost(entry, 100))
        self.assertEqual((None, None, "public-wg-default"), advisor.cost_for_path(self.paths, "public_wg", "r640", "rancher-panel"))

    def test_specific_path_overrides_wildcard(self):
        entry = advisor.path_entry(self.paths, "qianwen-nuclio-79", "harbor-registry", "aliyun_vpc")
        self.assertEqual("aliyun-vpc-placeholder", entry["id"])
        self.assertEqual("inactive", entry["status"])

    def test_tiered_cost_honors_free_quota(self):
        entry = {"status":"confirmed", "monthlyIncludedGiB":10, "monthlyFixedCost":2, "tiers":[{"fromGiB":0,"toGiB":100,"pricePerGiB":0.5},{"fromGiB":100,"toGiB":None,"pricePerGiB":0.2}]}
        self.assertEqual(2, advisor.tiered_cost(entry, 5))
        self.assertEqual(54, advisor.tiered_cost(entry, 120))


if __name__ == "__main__":
    unittest.main()
