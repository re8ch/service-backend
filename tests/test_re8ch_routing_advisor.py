import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "operations" / "scripts" / "re8ch-routing-advisor.py"
SPEC = importlib.util.spec_from_file_location("routing_advisor", MODULE_PATH)
ADVISOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADVISOR)


POLICY = {
    "metadata": {"version": "test-v1"},
    "hardGates": {
        "maximumLossRatio": 0.05,
        "maximumHandshakeAgeSeconds": 180,
        "requireBgpForPublicWireGuard": True,
        "requireBfdForPublicWireGuard": True,
        "rejectUnknownPriceWhenBudgetEnforced": True,
    },
    "pathPreference": {"aliyun_vpc": 300, "public_wg": 200, "zerotier": 100},
    "scoreWeights": {"preference": 10, "rttMilliseconds": -1, "jitterMilliseconds": -2, "lossRatio": -1000, "pricePerGiB": -50},
    "budget": {"enforced": False, "hardLimitRatio": 1.0},
    "consecutiveWinsRequired": 3,
    "holdDownSeconds": 300,
}


CATALOG = {"paths": [
    {"id": "public", "match": {"pathType": "public_wg"}, "pricePerGiB": 0.8, "budgetMonthly": 100, "status": "active"},
    {"id": "zt", "match": {"pathType": "zerotier"}, "pricePerGiB": 0.0, "budgetMonthly": None, "status": "active"},
]}


class AdvisorPolicyTests(unittest.TestCase):
    def test_public_wg_wins_when_healthy(self):
        public = ADVISOR.evaluate_path(POLICY, CATALOG, "public_wg", {"success": 1, "loss": 0, "rtt": 30, "bgp": 1, "bfd": 1, "handshake_age": 20})
        zt = ADVISOR.evaluate_path(POLICY, CATALOG, "zerotier", {"success": 1, "loss": 0, "rtt": 10})
        self.assertTrue(public["eligible"])
        self.assertGreater(public["score"], zt["score"])

    def test_failure_gate_rejects_public_wg(self):
        result = ADVISOR.evaluate_path(POLICY, CATALOG, "public_wg", {"success": 0, "loss": 1, "rtt": 0, "bgp": 0, "bfd": 0, "handshake_age": 999})
        self.assertFalse(result["eligible"])
        self.assertIn("bgp-down", result["reasons"])

    def test_budget_gate_rejects_exhausted_path(self):
        policy = {**POLICY, "budget": {"enforced": True, "hardLimitRatio": 1.0}}
        result = ADVISOR.evaluate_path(policy, CATALOG, "public_wg", {"success": 1, "loss": 0, "rtt": 20, "bgp": 1, "bfd": 1, "handshake_age": 10}, budget_spent=100)
        self.assertFalse(result["eligible"])
        self.assertIn("budget-exhausted", result["reasons"])

    def test_unknown_price_is_visible_but_allowed_before_enforcement(self):
        result = ADVISOR.evaluate_path(POLICY, {"paths": []}, "zerotier", {"success": 1, "loss": 0, "rtt": 10})
        self.assertTrue(result["eligible"])
        self.assertIsNone(result["price"])

    def test_oscillation_is_suppressed_until_three_consecutive_wins(self):
        state = None
        state, changed, _ = ADVISOR.advance_recommendation_state(POLICY, state, "public_wg", 1000)
        self.assertFalse(changed)
        state, changed, _ = ADVISOR.advance_recommendation_state(POLICY, state, "zerotier", 1030)
        self.assertFalse(changed)
        state, changed, _ = ADVISOR.advance_recommendation_state(POLICY, state, "public_wg", 1060)
        self.assertFalse(changed)
        state, changed, _ = ADVISOR.advance_recommendation_state(POLICY, state, "public_wg", 1090)
        self.assertFalse(changed)
        state, changed, reason = ADVISOR.advance_recommendation_state(POLICY, state, "public_wg", 1120)
        self.assertTrue(changed)
        self.assertEqual(reason, "accepted")

    def test_hold_down_blocks_immediate_reversal(self):
        state = {"recommended": "public_wg", "candidate": None, "wins": 0, "last_change": 1000}
        state, changed, reason = ADVISOR.advance_recommendation_state(POLICY, state, "zerotier", 1100)
        self.assertFalse(changed)
        self.assertEqual(reason, "hold-down")

    def test_route_map_recommendation_is_explicitly_non_executable(self):
        recommendation = ADVISOR.route_map_recommendation(
            POLICY, "node-a", "node-b", "zerotier", "public_wg", {}, 1234
        )
        self.assertEqual(recommendation["kind"], "FRRRouteMapRecommendation")
        self.assertEqual(recommendation["spec"]["recommendedLocalPreference"], 200)
        self.assertFalse(recommendation["spec"]["apply"])

    def test_quota_pressure_uses_tiers_without_fake_price(self):
        self.assertEqual(ADVISOR.quota_pressure(80, 100)["tier"], "warning")
        self.assertEqual(ADVISOR.quota_pressure(95, 100)["tier"], "critical")
        self.assertEqual(ADVISOR.quota_pressure(100, 100)["tier"], "exhausted")
        self.assertEqual(ADVISOR.quota_pressure(999, None)["tier"], "unlimited")


if __name__ == "__main__":
    unittest.main()
