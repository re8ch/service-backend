import importlib.util
import ipaddress
import json
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
            node("a1", "A", "spine", 65001, 1, "10.42.1.0/24"),
            node("a2", "A", "spine", 65002, 2, "10.42.2.0/24"),
            node("a3", "A", "spine", 65003, 3, "10.42.3.0/24"),
            node("a4", "A", "spine", 65004, 4, "10.42.4.0/24"),
            node("b1", "B", "spine", 65101, 5, "10.42.5.0/24"),
            node("b2", "B", "spine", 65102, 6, "10.42.6.0/24"),
            node("b3", "B", "spine", 65103, 7, "10.42.7.0/24"),
            node("leaf", "C", "leaf", 4200000008, 8, "10.42.8.0/24"),
        ]
        self.fabric = {"mode": "observe-only", "applyEnabled": False, "emergencyDisable": False,
                       "nativeRoutingCIDR": "10.42.0.0/16", "acceleratedCIDR": "10.250.0.0/24",
                       "serviceCIDR": "10.251.0.0/24", "nodes": self.nodes}

    def test_inventory_is_valid(self):
        self.assertTrue(AF.validate_fabric(self.fabric))

    def test_leaf_renderer_never_exports_default_or_node_identity(self):
        rendered = AF.render_frr(self.nodes[-1], self.fabric)
        self.assertNotIn("0.0.0.0/0", rendered)
        self.assertNotIn("10.181.22.0/24", rendered)
        self.assertEqual(rendered.count(" remote-as "), 7)

    def test_spine_transits_only_authorized_fabric_prefix_list(self):
        rendered = AF.render_frr(self.nodes[0], self.fabric)
        self.assertIn("prefix-list AF-IN out", rendered)
        self.assertNotIn("10.181.22.0/24", rendered)

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
            {"metadata": {"namespace": "x", "name": "b"}, "spec": {"priority": 100, "profile": "greedy", "podSelector": {}, "destination": {"cidrs": ["10.42.0.0/16"]}}},
            {"metadata": {"namespace": "x", "name": "a"}, "spec": {"priority": 100, "profile": "fastest", "podSelector": {}, "destination": {"cidrs": ["10.42.8.0/24"]}}},
        ]
        _, profile, cidr, _ = AF.resolve_policy(policies, "x", {}, "10.42.8.9")
        self.assertEqual((profile, cidr), ("fastest", "10.42.8.0/24"))

    def test_policy_bundle_uses_separate_marks_and_tables(self):
        bundle = AF.render_policy_bundle({"fastest": ["10.42.8.10"], "greedy": ["10.42.8.11"]},
                                         {"fastest": {"10.42.1.0/24": [{"via": "10.250.0.1", "dev": "wg0"}]}})
        self.assertIn("42241", bundle["nftables"])
        self.assertTrue(any("lookup 201" in rule for rule in bundle["ipRules"]))
        self.assertIn("table 201", bundle["ipRoutes"][0])

    def test_a1_r640_identity_uses_b1_transit_not_zerotier(self):
        routes = PODCIDR.control_plane_identity_routes("a1-wsl-zt")
        self.assertEqual(routes, ["ip route 10.181.22.128/32 wg-pt-a1 5"])
        self.assertNotIn("zto", routes[0])
        self.assertEqual(PODCIDR.control_plane_identity_routes("b1-wsl-zt"), [])
        self.assertEqual(PODCIDR.control_plane_identity_routes("r640"), [])

    def test_transport_spoke_advertises_each_wg_transport_as_next_hop(self):
        inventory = {
            "nativeRoutingCIDR": "10.42.0.0/16",
            "acceleratedCIDR": "10.250.0.0/24",
            "serviceCIDR": "10.43.0.0/16",
            "routeServers": [
                {"name": "a1", "asn": 65001, "internalIp": "10.181.22.147", "transportIp": "172.31.93.147"},
                {"name": "b1", "asn": 65002, "internalIp": "10.181.22.125", "transportIp": "172.31.94.125"},
            ],
            "nodes": [{"name": "leaf", "asn": 65010, "internalIp": "10.181.22.186",
                       "acceleratedIp": "10.250.0.186", "podCIDR": "10.42.21.0/24", "peerMode": "transport"}],
        }
        rendered = PODCIDR.render_spoke(inventory, inventory["nodes"][0])
        self.assertIn("set ip next-hop 172.31.93.186", rendered)
        self.assertIn("set ip next-hop 172.31.94.186", rendered)
        self.assertNotIn("set ip next-hop 10.181.22.186", rendered)

    def test_transport_leaf_allows_route_server_transit_sources(self):
        reconciler = (pathlib.Path(__file__).parents[1] / "clusters/re8ch/components/network/podcidr-fabric/reconciler.yaml").read_text()
        self.assertIn("transport_cidr=172.31.93.0/24", reconciler)
        self.assertIn("transport_cidr=172.31.94.0/24", reconciler)
        self.assertIn('[ "${peer_mode}" = transport ]', reconciler)
        self.assertIn('ip route ${transport_cidr} ${iface} 5', reconciler)

    def test_podcidr_fabric_refreshes_multi_pool_allocations_without_losing_last_good(self):
        root = pathlib.Path(__file__).parents[1]
        reconciler = (
            root / "operations/network/manifests/network/podcidr-fabric/reconciler.yaml"
        ).read_text()

        self.assertIn("refresh_cilium_nodes()", reconciler)
        self.assertIn("refresh_cilium_nodes_or_reload()", reconciler)
        self.assertEqual(reconciler.count("refresh_cilium_nodes_or_reload\n"), 3)
        self.assertIn('mv "${candidate}" "${cilium_nodes}"', reconciler)
        self.assertIn("retaining last-known-good allocations", reconciler)
        self.assertIn('exec /bin/sh /config/reconcile.sh', reconciler)
        resources = [item for item in yaml.safe_load_all(reconciler) if item]
        daemonset = next(item for item in resources if item["kind"] == "DaemonSet")
        self.assertEqual(
            daemonset["spec"]["template"]["metadata"]["annotations"][
                "networking.re8ch.com/config-revision"
            ],
            "multipool-relay-v17",
        )

    def test_control_plane_readiness_requires_both_route_servers_and_fib(self):
        root = pathlib.Path(__file__).parents[1]
        inventory = json.loads(
            (root / "clusters/re8ch/components/network/podcidr-fabric/inventory.json").read_text()
        )
        reconciler = (
            root / "clusters/re8ch/components/network/podcidr-fabric/reconciler.yaml"
        ).read_text()

        self.assertEqual(
            inventory["readinessPodCIDRs"],
            ["10.42.6.0/24", "10.42.10.0/24", "10.42.13.0/24"],
        )
        self.assertIn('.peers[$peer].state == "Established"', reconciler)
        self.assertIn('.peers[$a1].state == "Established"', reconciler)
        self.assertIn('.peers[$b1].state == "Established"', reconciler)
        self.assertIn('ip -4 route show exact "${prefix}"', reconciler)

    def test_podcidr_fabric_uses_symmetric_transport_next_hops_on_control_planes(self):
        root = pathlib.Path(__file__).parents[1]
        resources = [
            item for item in yaml.safe_load_all(
                (root / "clusters/re8ch/components/network/podcidr-fabric/reconciler.yaml").read_text()
            ) if item
        ]
        config = next(item for item in resources if item["kind"] == "ConfigMap")
        script = config["data"]["reconcile.sh"]
        self.assertIn("POD-FABRIC-RR-TRANSPORT-OUT", script)
        self.assertIn('set ip next-hop ${transport_ip}', script)
        self.assertIn('set ip next-hop ${internal_ip}', script)
        daemonset = next(item for item in resources if item["kind"] == "DaemonSet")
        terms = daemonset["spec"]["template"]["spec"]["affinity"]["nodeAffinity"][
            "requiredDuringSchedulingIgnoredDuringExecution"
        ]["nodeSelectorTerms"]
        assert any(
            expression["key"] == "kubernetes.io/hostname"
            and expression["operator"] == "In"
            and set(expression["values"]) == {
                "r640", "a1-wsl-zt", "b1-wsl-zt", "mubao-wsl", "overseas-flk", "overseas-kl",
                "overseas-la", "overseas-la-2"
            }
            for term in terms for expression in term["matchExpressions"]
        )

    def test_release_pins_dns_helm_test_to_healthy_peer(self):
        release_path = pathlib.Path(__file__).parents[1] / "platform" / "advanced-fabric" / "release.yaml"
        resources = [doc for doc in yaml.safe_load_all(release_path.read_text()) if doc]
        release = next(doc for doc in resources if doc["kind"] == "HelmRelease")
        dns = release["spec"]["values"]["advancedFabric"]["dns"]
        self.assertEqual(dns["testNodeSelector"], {"kubernetes.io/hostname": "r640"})

    def test_release_places_controller_on_server_and_api_relay(self):
        release_path = pathlib.Path(__file__).parents[1] / "platform" / "advanced-fabric" / "release.yaml"
        resources = [doc for doc in yaml.safe_load_all(release_path.read_text()) if doc]
        release = next(doc for doc in resources if doc["kind"] == "HelmRelease")
        controller = release["spec"]["values"]["advancedFabric"]["controller"]
        self.assertEqual(controller["replicas"], 2)
        self.assertEqual(controller["pdbMinAvailable"], 1)
        self.assertIs(controller["useNodeLocalAPI"], True)
        self.assertEqual(controller["nodeLocalAPIPort"], 6443)
        self.assertEqual(
            controller["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"],
            ["a1-wsl-zt", "r640"],
        )


if __name__ == "__main__":
    unittest.main()
