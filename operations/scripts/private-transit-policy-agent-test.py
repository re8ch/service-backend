#!/usr/bin/env python3
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("private-transit-policy-agent.py")
POLICY = Path(__file__).parents[1] / "network" / "manifests" / "network" / "private-transit" / "policy-agent.json"
INVENTORY = Path(__file__).parents[2] / "clusters" / "re8ch" / "components" / "network" / "podcidr-fabric" / "inventory.json"
SPEC = importlib.util.spec_from_file_location("policy_agent", SCRIPT)
AGENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AGENT)


class PolicyAgentTest(unittest.TestCase):
    def setUp(self):
        self.config = {
            "minimumHoldSeconds": 60,
            "weightChangeThresholdPercent": 20,
            "decisionTable": 15300,
            "gateways": [
                {"name": "a1", "address": "192.0.2.176", "staticWeight": 100, "capacityMbps": 1000},
                {"name": "b1", "address": "192.0.2.4", "staticWeight": 100, "capacityMbps": 1000},
            ],
            "targets": [{"name": "edge", "address": "192.0.2.81", "staticPreference": {"a1": 100, "b1": 100}}],
        }

    def test_gateway_nexthop_prefers_local_tunnel(self):
        gateway = {"address": "192.0.2.176", "interface": "wg-pt-a1"}
        with patch.object(AGENT, "interface_exists", return_value=True):
            self.assertEqual(AGENT.gateway_nexthop(gateway), ["dev", "wg-pt-a1"])

    def test_dynamic_targets_match_the_accelerated_inventory(self):
        config = json.loads(POLICY.read_text())
        inventory = json.loads(INVENTORY.read_text())
        targets = {(item["name"], item["address"]) for item in config["targets"]}
        expected = {(item["name"], item["acceleratedIp"]) for item in inventory["nodes"]}
        self.assertEqual(targets, expected)

    def test_gateway_nexthop_workers_use_zt_gateway(self):
        gateway = {"address": "192.0.2.176", "interface": "wg-pt-a1"}
        with patch.object(AGENT, "interface_exists", return_value=False):
            self.assertEqual(AGENT.gateway_nexthop(gateway), ["via", "192.0.2.176"])

    def test_ensure_rule_keeps_matching_live_rule(self):
        current = unittest.mock.MagicMock(stdout="11530: from all to 192.0.2.65/24 lookup 15300\n")
        with patch.object(AGENT, "run", return_value=current) as run:
            AGENT.ensure_rule(11530, ["to", "192.0.2.65/24", "lookup", "15300"], ["to 192.0.2.65/24", "lookup 15300"])
        run.assert_called_once_with("ip", "rule", "show", "priority", "11530", check=False)

    def test_ensure_rule_restores_missing_live_rule(self):
        missing = unittest.mock.MagicMock(stdout="")
        with patch.object(AGENT, "run", side_effect=[missing, unittest.mock.MagicMock(), unittest.mock.MagicMock()]) as run:
            AGENT.ensure_rule(11530, ["to", "192.0.2.65/24", "lookup", "15300"], ["to 192.0.2.65/24", "lookup 15300"])
        self.assertEqual(run.call_args_list[-1].args, ("ip", "rule", "add", "priority", "11530", "to", "192.0.2.65/24", "lookup", "15300"))

    def test_reconcile_selected_routes_reinstalls_last_decision(self):
        state = {"edge": {"weights": {"a1": 1}, "lastChange": 900}}
        with patch.object(AGENT, "apply_route") as apply:
            AGENT.reconcile_selected_routes(self.config, state)
        apply.assert_called_once_with(self.config, self.config["targets"][0], {"a1": 1}, dry_run=False)

    def test_loss_removes_failed_gateway(self):
        measurements = {
            "a1": {"lossPercent": 0, "rttMs": 20},
            "b1": {"lossPercent": 100, "rttMs": None},
        }
        with patch.object(AGENT, "probe", side_effect=lambda target, gateway, source, *args: measurements[gateway["name"]]), patch.object(AGENT, "apply_route") as apply:
            state = AGENT.iteration(self.config, "192.0.2.117", {"edge": {"weights": {"a1": 1, "b1": 1}, "lastChange": 990}}, 1000)
        self.assertEqual(state["edge"]["weights"], {"a1": 1})
        apply.assert_called_once()

    def test_hysteresis_blocks_small_early_change(self):
        measurements = {
            "a1": {"lossPercent": 0, "rttMs": 20},
            "b1": {"lossPercent": 0, "rttMs": 22},
        }
        previous = {"edge": {"weights": {"a1": 1, "b1": 1}, "lastChange": 990}}
        with patch.object(AGENT, "probe", side_effect=lambda target, gateway, source, *args: measurements[gateway["name"]]), patch.object(AGENT, "apply_route") as apply:
            state = AGENT.iteration(self.config, "192.0.2.117", previous, 1000)
        self.assertEqual(state["edge"]["weights"], {"a1": 1, "b1": 1})
        apply.assert_not_called()

    def test_bfd_withdrawal_prunes_selected_gateway(self):
        state = {"edge": {"weights": {"a1": 1, "b1": 1}, "lastChange": 900}}
        with patch.object(AGENT, "interface_exists", return_value=False), patch.object(AGENT, "bgp_gateways", return_value={"192.0.2.176"}), patch.object(AGENT, "apply_route") as apply:
            result = AGENT.prune_with_bgp(self.config, state, 1000)
        self.assertEqual(result["edge"]["weights"], {"a1": 1})
        self.assertIn("BGP/BFD", result["edge"]["reason"])
        apply.assert_called_once()

    def test_tunnel_node_skips_worker_bgp_pruning(self):
        state = {"edge": {"weights": {"a1": 1, "b1": 1}, "lastChange": 900}}
        self.config["gateways"][0]["interface"] = "wg-pt-a1"
        with patch.object(AGENT, "interface_exists", return_value=True), patch.object(AGENT, "bgp_gateways") as bgp, patch.object(AGENT, "apply_route") as apply:
            result = AGENT.prune_with_bgp(self.config, state, 1000)
        self.assertEqual(result, state)
        bgp.assert_not_called()
        apply.assert_not_called()

    def test_interface_utilization_reduces_score(self):
        target = self.config["targets"][0]
        gateway = self.config["gateways"][0]
        idle = AGENT.score(target, gateway, {"lossPercent": 0, "rttMs": 20, "utilizationPercent": 0})
        busy = AGENT.score(target, gateway, {"lossPercent": 0, "rttMs": 20, "utilizationPercent": 80})
        self.assertLess(busy, idle)
        self.assertAlmostEqual(busy / idle, 0.2)

    def test_full_duplex_utilization_uses_busier_direction(self):
        gateway = {"name": "a1", "interface": "wg-pt-a1", "capacityMbps": 100}
        previous = {"a1": {"rxBytes": 1000, "txBytes": 2000, "time": 10}}
        # Over one second RX is 80 Mbps and TX is 40 Mbps; utilization is 80%,
        # not their 120% sum.
        with patch.object(AGENT, "read_interface_bytes", return_value=(10_001_000, 5_002_000)):
            values, _ = AGENT.sample_utilization([gateway], previous, 11)
        self.assertAlmostEqual(values["a1"], 80.0)

    def test_fresh_remote_utilization_fills_unknown_value(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = b'{"utilizationPercent":37.5,"updatedAt":1000}'
        with patch.object(AGENT.time, "time", return_value=1005), patch.object(AGENT.urllib.request, "urlopen", return_value=response):
            values = AGENT.fetch_gateway_utilization(self.config, {"a1": None, "b1": 12.0})
        self.assertEqual(values, {"a1": 37.5, "b1": 12.0})


if __name__ == "__main__":
    unittest.main()
