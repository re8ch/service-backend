import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "scripts" / "re8ch-aliyun-cost-facts.py"
SPEC = importlib.util.spec_from_file_location("aliyun_cost_facts", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AliyunCostFactsTests(unittest.TestCase):
    def test_extracts_realized_network_price(self):
        payload = {"Data": {"Items": {"Item": [{
            "ProductCode": "cdt_DataTransfer_public_cn",
            "BillingItemCode": "internet_traffic",
            "InstanceID": "i-test",
            "Usage": "10",
            "UsageUnit": "GB",
            "PretaxAmount": "8",
            "Currency": "CNY",
        }]}}}
        fact = MODULE.normalize(payload, "2026-08")[0]
        self.assertEqual(fact["realizedPricePerGiB"], 0.8)
        self.assertEqual(fact["billingItemCode"], "internet_traffic")

    def test_does_not_invent_price_for_unknown_unit(self):
        payload = {"Data": {"Items": {"Item": [{
            "ProductName": "跨地域流量",
            "Usage": "10",
            "UsageUnit": "unknown",
            "PretaxAmount": "8",
        }]}}}
        self.assertIsNone(MODULE.normalize(payload, "2026-08")[0]["realizedPricePerGiB"])

    def test_ignores_non_network_bill(self):
        payload = {"Data": {"Items": {"Item": [{"ProductCode": "rds", "ProductName": "RDS"}]}}}
        self.assertEqual(MODULE.normalize(payload, "2026-08"), [])


if __name__ == "__main__":
    unittest.main()
