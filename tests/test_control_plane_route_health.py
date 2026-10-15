import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "operations/network/manifests/network/private-transit"


class ControlPlaneRouteHealthTest(unittest.TestCase):
    def test_route_health_does_not_gate_k3s_or_remote_api(self):
        service = (MANIFESTS / "re8ch-wg-route-health.service").read_text()
        script = (MANIFESTS / "re8ch-wg-route-health").read_text()
        combined = service + script

        for forbidden in (
            "Before=k3s.service",
            "WAIT_FOR_API",
            "API_TARGET",
            "API_PORT",
            "ExecStartPre",
        ):
            self.assertNotIn(forbidden, combined)

    def test_per_node_environments_only_select_routes(self):
        for name in (
            "re8ch-wg-route-health-a1.env",
            "re8ch-wg-route-health-b1.env",
        ):
            content = (MANIFESTS / name).read_text()
            self.assertIn("WG_INTERFACE=", content)
            self.assertIn("LOCAL_NODE_IP=", content)
            self.assertIn("ROUTE_TARGETS=", content)
            self.assertNotIn("API_", content)
            self.assertNotIn("WAIT_", content)


if __name__ == "__main__":
    unittest.main()
