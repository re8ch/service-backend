import importlib.util
import ipaddress
import pathlib
import unittest

import yaml


PATH = pathlib.Path(__file__).parents[1] / "operations" / "scripts" / "advanced_fabric.py"
SPEC = importlib.util.spec_from_file_location("advanced_fabric", PATH)
AF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AF)

PODCIDR_PATH = pathlib.Path(__file__).parents[1] / "operations" / "scripts" / "frr" / "podcidr_fabric.py"
PODCIDR_SPEC = importlib.util.spec_from_file_location("podcidr_fabric_test", PODCIDR_PATH)
PODCIDR = importlib.util.module_from_spec(PODCIDR_SPEC)
PODCIDR_SPEC.loader.exec_module(PODCIDR)


def node(name, klass, role, asn, octet, pod, complete=True, quota=None, bandwidth=200):
    item = {"name": name, "class": klass, "role": role, "asn": asn,
            "internalIP": f"10.181.22.{octet}", "acceleratedIP": f"10.250.0.{octet}",
            "podCIDR": pod, "uplinkMbps": bandwidth, "inventoryComplete": complete}
    if quota is not None:
        item["monthlyTrafficLimitGiB"] = quota
    return item


class AdvancedFabricTests(unittest.TestCase):
    def setUp(self):
        self.nodes = [
            node("a1", "A", "spine", 65001, 1, "192.0.2.110/24"),
            node("a2", "A", "spine", 65002, 2, "192.0.2.114/24"),
            node("a3", "A", "spine", 65003, 3, "192.0.2.203/24"),
            node("a4", "A", "spine", 65004, 4, "192.0.2.165/24"),
            node("b1", "B", "spine", 65101, 5, "192.0.2.113/24"),
            node("b2", "B", "spine", 65102, 6, "192.0.2.140/24"),
            node("b3", "B", "spine", 65103, 7, "192.0.2.134/24"),
            node("leaf", "C", "leaf", 4200000008, 8, "192.0.2.184/24"),
        ]
        self.fabric = {"mode": "observe-only", "applyEnabled": False, "emergencyDisable": False,
                       "nativeRoutingCIDR": "192.0.2.57/16", "acceleratedCIDR": "192.0.2.65/24",
                       "serviceCIDR": "192.0.2.121/24", "nodes": self.nodes}

    def test_inventory_is_valid(self):
        self.assertTrue(AF.validate_fabric(self.fabric))

    def test_leaf_renderer_never_exports_default_or_node_identity(self):
        rendered = AF.render_frr(self.nodes[-1], self.fabric)
        self.assertNotIn("0.0.0.0/0", rendered)
        self.assertNotIn("192.0.2.20/24", rendered)
        self.assertEqual(rendered.count(" remote-as "), 7)

    def test_spine_transits_only_authorized_fabric_prefix_list(self):
        rendered = AF.render_frr(self.nodes[0], self.fabric)
        self.assertIn("prefix-list AF-IN out", rendered)
        self.assertNotIn("192.0.2.20/24", rendered)

    def test_asn_compatibility_phase_uses_dual_as(self):
        migrated = {**self.nodes[0], "legacyASN": 4200000001}
        rendered = AF.render_frr(migrated, {**self.fabric, "nodes": [migrated] + self.nodes[1:]}, "compatibility")
        self.assertIn("router bgp 4200000001", rendered)
        self.assertIn("local-as 65001 no-prepend replace-as dual-as", rendered)
        self.assertIn("remote-as external", rendered)

    def test_incomplete_spine_is_ineligible(self):
        candidate = {"node": {**self.nodes[0], "inventoryComplete": False},
                     "facts": {"bgp": True, "bfd": True, "nodeReady": True, "loss": 0}}
        self.assertEqual(AF.eligible_paths("fastest", [candidate]), [])

    def test_quota_pressure_has_required_tiers(self):
        self.assertEqual(AF.quota_pressure(80, 100)["tier"], "warning")
        self.assertEqual(AF.quota_pressure(95, 100)["tier"], "critical")
        self.assertEqual(AF.quota_pressure(100, 100)["tier"], "exhausted")
        self.assertEqual(AF.quota_pressure(1000, None)["tier"], "unlimited")

    def test_policy_conflict_order(self):
        policies = [
            {"metadata": {"namespace": "x", "name": "b"}, "spec": {"priority": 100, "profile": "greedy", "podSelector": {}, "destination": {"cidrs": ["192.0.2.57/16"]}}},
            {"metadata": {"namespace": "x", "name": "a"}, "spec": {"priority": 100, "profile": "fastest", "podSelector": {}, "destination": {"cidrs": ["192.0.2.184/24"]}}},
        ]
        _, profile, cidr, _ = AF.resolve_policy(policies, "x", {}, "192.0.2.206")
        self.assertEqual((profile, cidr), ("fastest", "192.0.2.184/24"))

    def test_policy_bundle_uses_separate_marks_and_tables(self):
        bundle = AF.render_policy_bundle({"fastest": ["192.0.2.178"], "greedy": ["192.0.2.125"]},
                                         {"fastest": {"192.0.2.110/24": [{"via": "192.0.2.116", "dev": "wg0"}]}})
        self.assertIn("42241", bundle["nftables"])
        self.assertTrue(any("lookup 201" in rule for rule in bundle["ipRules"]))
        self.assertIn("table 201", bundle["ipRoutes"][0])

    def test_a1_r640_identity_uses_b1_transit_not_zerotier(self):
        routes = PODCIDR.control_plane_identity_routes("restricted-node-1eb0570f74")
        self.assertEqual(routes, ["ip route 192.0.2.178/32 wg-pt-a1 5"])
        self.assertNotIn("zto", routes[0])
        self.assertEqual(PODCIDR.control_plane_identity_routes("restricted-node-296f36f2fb"), [])
        self.assertEqual(PODCIDR.control_plane_identity_routes("restricted-node-6dc3f140f9"), [])

    def test_transport_spoke_advertises_each_wg_transport_as_next_hop(self):
        inventory = {
            "nativeRoutingCIDR": "192.0.2.57/16",
            "acceleratedCIDR": "192.0.2.65/24",
            "serviceCIDR": "192.0.2.205/16",
            "routeServers": [
                {"name": "a1", "asn": 65001, "internalIp": "192.0.2.176", "transportIp": "192.0.2.178"},
                {"name": "b1", "asn": 65002, "internalIp": "192.0.2.4", "transportIp": "192.0.2.101"},
            ],
            "nodes": [{"name": "leaf", "asn": 65010, "internalIp": "192.0.2.89",
                       "acceleratedIp": "192.0.2.46", "podCIDR": "192.0.2.94/24", "peerMode": "transport"}],
        }
        rendered = PODCIDR.render_spoke(inventory, inventory["nodes"][0])
        self.assertIn("set ip next-hop 192.0.2.201", rendered)
        self.assertIn("set ip next-hop 192.0.2.108", rendered)
        self.assertNotIn("set ip next-hop 192.0.2.89", rendered)

    def test_transport_leaf_allows_route_server_transit_sources(self):
        reconciler = (pathlib.Path(__file__).parents[1] / "clusters/re8ch/components/network/podcidr-fabric/reconciler.yaml").read_text()
        self.assertIn("transport_cidr=192.0.2.63/24", reconciler)
        self.assertIn("transport_cidr=192.0.2.208/24", reconciler)
        self.assertIn('[ "${peer_mode}" = transport ]', reconciler)
        self.assertIn('ip route ${transport_cidr} ${iface} 5', reconciler)

    def test_release_pins_dns_helm_test_to_healthy_peer(self):
        release_path = pathlib.Path(__file__).parents[1] / "platform" / "advanced-fabric" / "release.yaml"
        resources = [doc for doc in yaml.safe_load_all(release_path.read_text()) if doc]
        release = next(doc for doc in resources if doc["kind"] == "HelmRelease")
        dns = release["spec"]["values"]["advancedFabric"]["dns"]
        self.assertEqual(dns["testNodeSelector"], {"kubernetes.io/hostname": "restricted-node-1eb0570f74"})

    def test_release_pins_controller_to_verified_control_plane_api_path(self):
        release_path = pathlib.Path(__file__).parents[1] / "platform" / "advanced-fabric" / "release.yaml"
        resources = [doc for doc in yaml.safe_load_all(release_path.read_text()) if doc]
        release = next(doc for doc in resources if doc["kind"] == "HelmRelease")
        controller = release["spec"]["values"]["advancedFabric"]["controller"]
        self.assertEqual(controller["nodeSelector"], {"kubernetes.io/hostname": "restricted-node-1eb0570f74"})


if __name__ == "__main__":
    unittest.main()
