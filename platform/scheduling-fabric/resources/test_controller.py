import importlib.util
from pathlib import Path
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("penrose_controller", Path(__file__).with_name("controller.py"))
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)

class ScoreTests(unittest.TestCase):
    def test_service_requires_real_selector_endpoints_and_replicas(self):
        service={"metadata":{"name":"api","namespace":"test"},"spec":{"selector":{"app":"api"}}}
        pod={"metadata":{"name":"api-x","namespace":"test","labels":{"app":"api"},"ownerReferences":[{"kind":"ReplicaSet","name":"api-rs"}]},"spec":{"nodeName":"n1","schedulerName":"default-scheduler","containers":[{"resources":{"requests":{"cpu":"100m","memory":"100Mi"}}}]},"status":{"phase":"Running","containerStatuses":[{"restartCount":0}]}}
        rs={"metadata":{"name":"api-rs","ownerReferences":[{"kind":"Deployment","name":"api"}]}}
        dep={"metadata":{"name":"api"},"spec":{"replicas":1}}
        endpoint={"metadata":{"labels":{"kubernetes.io/service-name":"api"}},"endpoints":[{"conditions":{"ready":True}}]}
        metrics={"metadata":{"name":"api-x"},"containers":[{"usage":{"cpu":"70m","memory":"70Mi"}}]}
        node={"metadata":{"name":"n1","labels":{"scheduling.re8ch.com/failure-domain":"fd1"}}}
        result=controller.score_service(service,[endpoint],[pod],[metrics],[node],[rs],[dep],[])
        self.assertTrue(result["eligible"])
        self.assertTrue(result["observation"]["complete"])
        self.assertEqual(result["observation"]["scores"],{"resilience":100,"efficiency":70,"stability":100})

    def test_missing_metrics_is_unknown_not_zero(self):
        service={"metadata":{"name":"api","namespace":"test"},"spec":{"selector":{"app":"api"}}}
        result=controller.score_service(service,[],[],[],[],[],[],[])
        self.assertFalse(result["eligible"])
        self.assertIsNone(result["observation"]["scores"]["efficiency"])
        self.assertFalse(result["observation"]["complete"])

    def test_empty_endpoint_slice_does_not_stop_reconciliation(self):
        service = {"metadata": {"name": "api", "namespace": "test"}, "spec": {"selector": {"app": "api"}}}
        empty_slice = {"metadata": {"labels": {"kubernetes.io/service-name": "api"}}, "endpoints": None}
        result = controller.score_service(service, [empty_slice], [], [], [], [], [], [])
        self.assertFalse(result["eligible"])
        self.assertEqual(result["observation"]["evidence"]["readyEndpoints"], 0)

    def test_quantity_parsers(self):
        self.assertAlmostEqual(controller.cpu("750m"),.75)
        self.assertEqual(controller.memory("2Gi"),2*1024**3)
        self.assertEqual(controller.memory("10M"),10_000_000)

    def test_profile_projects_owned_service_plane(self):
        labels = controller.profile_labels({
            "background": {"provider": "tencent", "environment": "cloud"},
            "reliability": {"class": "stable"},
            "reservationProfile": "worker-small",
            "failureDomains": {"host": "zan"},
            "capabilities": {"servicePlane": "core"},
        }, "zan")
        self.assertEqual(labels["scheduling.re8ch.com/service-plane"], "core")

    def test_profile_omits_unassigned_service_plane(self):
        labels = controller.profile_labels({}, "worker")
        self.assertNotIn("scheduling.re8ch.com/service-plane", labels)

    def test_profile_projects_reviewed_pool_membership_without_host_selectors(self):
        labels = controller.profile_labels({
            "capabilities": {"placementPools": ["service-core-local", "service-core-ab"]},
        }, "restricted-node-1eb0570f74")
        self.assertEqual(labels["scheduling.re8ch.com/pool-service-core-local"], "true")
        self.assertEqual(labels["scheduling.re8ch.com/pool-service-core-ab"], "true")
        self.assertEqual(labels["scheduling.re8ch.com/pool-service-core-r640"], "false")

    def test_profile_projects_bounded_inventory_capabilities(self):
        labels = controller.profile_labels({
            "background": {"provider": "overseas", "environment": "cross-border", "costClass": "metered-bandwidth"},
            "reliability": {"class": "stable"}, "capabilities": {"storage": ["ceph-client"], "egress": True, "scheduling": ["dynamic"]},
        }, "edge")
        self.assertEqual(labels["scheduling.re8ch.com/cost-class"], "metered-bandwidth")
        self.assertEqual(labels["scheduling.re8ch.com/capability-storage-ceph-client"], "true")
        self.assertEqual(labels["scheduling.re8ch.com/capability-egress"], "true")
        self.assertEqual(labels["scheduling.re8ch.com/placement-general-reliable"], "true")

    def test_placement_excludes_control_plane_when_class_disallows_it(self):
        placement = {"spec": {"workloadMode": "PortableService", "nodeSelector": {"scheduling.re8ch.com/placement-general-reliable": "true"}, "allowControlPlane": False}}
        nodes = [
            {"metadata": {"name": "control", "labels": {"scheduling.re8ch.com/placement-general-reliable": "true", "scheduling.re8ch.com/workload-eligible": "true", "scheduling.re8ch.com/capacity-window": "open", "node-role.kubernetes.io/control-plane": "true"}}},
            {"metadata": {"name": "worker", "labels": {"scheduling.re8ch.com/placement-general-reliable": "true", "scheduling.re8ch.com/workload-eligible": "true", "scheduling.re8ch.com/capacity-window": "open"}}},
        ]
        self.assertEqual([n["metadata"]["name"] for n in controller.placement_eligible_nodes(placement, nodes)], ["worker"])

    def test_portable_placement_excludes_stale_and_closed_nodes(self):
        placement = {"spec": {"workloadMode": "PortableService", "nodeSelector": {"pool": "stable"}, "allowControlPlane": True}}
        nodes = [
            {"metadata": {"name": "ready", "labels": {"pool": "stable", "scheduling.re8ch.com/workload-eligible": "true", "scheduling.re8ch.com/capacity-window": "open"}}},
            {"metadata": {"name": "stale", "labels": {"pool": "stable", "scheduling.re8ch.com/workload-eligible": "false", "scheduling.re8ch.com/capacity-window": "open"}}},
            {"metadata": {"name": "closed", "labels": {"pool": "stable", "scheduling.re8ch.com/workload-eligible": "true", "scheduling.re8ch.com/capacity-window": "closed"}}},
        ]
        self.assertEqual([n["metadata"]["name"] for n in controller.placement_eligible_nodes(placement, nodes)], ["ready"])

    def test_bootstrap_daemon_keeps_registered_nodes_in_its_coverage(self):
        placement = {"spec": {"workloadMode": "NodeBootstrapDaemon", "nodeSelector": {"kubernetes.io/os": "linux"}, "allowControlPlane": True}}
        node = {"metadata": {"name": "joining", "labels": {"kubernetes.io/os": "linux", "scheduling.re8ch.com/workload-eligible": "false", "scheduling.re8ch.com/capacity-window": "unknown"}}}
        self.assertEqual(controller.placement_eligible_nodes(placement, [node]), [node])

    def test_placement_capacity_fails_closed_until_node_and_domain_minima_hold(self):
        placement = {"spec": {"workloadMode": "PortableService", "nodeSelector": {"pool": "core"}, "allowControlPlane": True,
                              "topologyKey": "scheduling.re8ch.com/failure-domain", "minEligibleNodes": 2, "minFailureDomains": 2}}
        nodes = [{"metadata": {"name": "one", "labels": {"pool": "core", "scheduling.re8ch.com/workload-eligible": "true",
                  "scheduling.re8ch.com/capacity-window": "open", "scheduling.re8ch.com/failure-domain": "fd1"}}}]
        _, domains, available, _ = controller.placement_capacity(placement, nodes)
        self.assertEqual(domains, {"fd1"})
        self.assertFalse(available)

    def test_pool_membership_is_a_hard_upper_bound(self):
        placement = {"spec": {"workloadMode": "PortableService", "nodePool": "service-core-ab",
                              "nodeSelector": {"scheduling.re8ch.com/pool-service-core-ab": "true"},
                              "allowControlPlane": True}}
        unrelated = {"metadata": {"name": "healthy-cloud", "labels": {
            "scheduling.re8ch.com/workload-eligible": "true",
            "scheduling.re8ch.com/capacity-window": "open",
        }}}
        self.assertEqual(controller.placement_node_rejection(placement, unrelated), "outside-allowed-pool")

    @patch.object(controller, "patch")
    @patch.object(controller, "items")
    def test_unavailable_placement_class_keeps_definition_ready(self, items, patch_status):
        items.return_value = [{"metadata": {"name": "gpu-rocm", "generation": 1},
                               "spec": {"nodeSelector": {"scheduling.re8ch.com/capability-gpu": "rocm-wsl"}}}]
        controller.reconcile_placement_classes([])
        status = patch_status.call_args.args[1]["status"]
        self.assertEqual(status["eligibleNodes"], 0)
        self.assertEqual([(c["type"], c["status"]) for c in status["conditions"]],
                         [("Ready", "True"), ("Available", "False")])

    def test_capacity_window_closes_and_reopens_with_hysteresis(self):
        gib = 1024 ** 3
        node = {"status": {"conditions": [{"type": "Ready", "status": "True"}], "allocatable": {
            "cpu": "4", "memory": "8Gi", "ephemeral-storage": "80Gi", "pods": "100"}}}
        measurement = {"mounts": {("/", "/dev/sda", "ext4"): {"size": 100*gib, "available": 12*gib,
            "readonly": 0, "deviceError": 0, "files": 1000, "filesFree": 900}},
            "memoryTotal": 8*gib, "memoryAvailable": 4*gib, "cpuRatio": 0.3}
        state, reasons = controller.capacity_window(node, measurement)
        self.assertEqual(state, "closed")
        self.assertIn("filesystem-low:/", reasons)
        self.assertEqual(controller.capacity_window(node, measurement, previously_closed=True)[0], "closed")
        measurement["mounts"][("/", "/dev/sda", "ext4")]["available"] = 25*gib
        self.assertEqual(controller.capacity_window(node, measurement, previously_closed=True)[0], "open")

    def test_capacity_window_fails_unknown_when_disk_telemetry_missing(self):
        node = {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        state, _ = controller.capacity_window(node, {"memoryTotal": 1024, "memoryAvailable": 1024, "cpuRatio": 0})
        self.assertEqual(state, "unknown")

    @patch.object(controller, "metric_rows")
    def test_cpu_uses_node_exporter_even_without_trimaran(self, rows):
        def answer(query):
            if "node_cpu_seconds_total" in query:
                return [({"node": "n1"}, 0.35)]
            return []
        rows.side_effect = answer
        self.assertEqual(controller.capacity_measurements()["n1"]["cpuRatio"], 0.35)
        self.assertTrue(any("node_cpu_seconds_total" in call.args[0] for call in rows.call_args_list))

    def test_capacity_window_accounts_for_reserved_ephemeral_storage(self):
        gib = 1024 ** 3
        node = {"status": {"conditions": [{"type": "Ready", "status": "True"}], "allocatable": {"ephemeral-storage": "80Gi"}}}
        measurement = {"mounts": {("/", "/dev/sda", "ext4"): {"size": 100*gib, "available": 50*gib,
            "readonly": 0, "deviceError": 0}}, "memoryTotal": 8*gib, "memoryAvailable": 6*gib, "cpuRatio": 0.1}
        state, reasons = controller.capacity_window(node, measurement, {"ephemeral-storage": 73*gib})
        self.assertEqual(state, "closed")
        self.assertIn("requests-high:ephemeral-storage", reasons)

    @patch.object(controller, "request")
    @patch.object(controller, "patch")
    @patch.object(controller, "items", return_value=[])
    @patch.object(controller, "capacity_measurements")
    def test_capacity_taint_preserves_other_taints(self, metrics, _items, patch_node, request):
        gib = 1024 ** 3
        node = {"metadata": {"name": "n1", "resourceVersion": "1", "labels": {}, "annotations": {}},
                "spec": {"taints": [{"key": "dedicated", "value": "storage", "effect": "NoSchedule"}]},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        sample = {"mounts": {("/", "/dev/sda", "ext4"): {"size": 100*gib, "available": 2*gib,
                  "readonly": 0, "deviceError": 0}}, "memoryTotal": 8*gib,
                  "memoryAvailable": 6*gib, "cpuRatio": 0.2}
        metrics.return_value = {"n1": sample}
        patch_node.return_value = {"metadata": {"resourceVersion": "2"}}
        controller.reconcile_capacity_windows([node])
        taints = request.call_args.args[2][-1]["value"]
        self.assertEqual(taints[0]["key"], "dedicated")
        self.assertEqual(taints[1]["key"], controller.CAPACITY_TAINT["key"])
        self.assertEqual(request.call_args.args[2][0]["value"], "2")

    @patch.object(controller, "request")
    @patch.object(controller, "patch")
    @patch.object(controller, "items", return_value=[])
    @patch.object(controller, "capacity_measurements", return_value={})
    def test_first_missing_sample_closes_ready_node(self, _metrics, _items, patch_node, request):
        node = {"metadata": {"name": "n1", "resourceVersion": "1", "labels": {}, "annotations": {}},
                "spec": {"taints": []}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        patch_node.return_value = {"metadata": {"resourceVersion": "2"}}
        controller.reconcile_capacity_windows([node])
        self.assertEqual(node["metadata"]["labels"]["scheduling.re8ch.com/capacity-window"], "unknown")
        self.assertIn(controller.CAPACITY_TAINT, request.call_args.args[2][-1]["value"])

    def test_reliability_requires_stable_ready_window(self):
        node = {"metadata": {"name": "worker"}, "spec": {}, "status": {"conditions": [
            {"type": "Ready", "status": "True", "lastTransitionTime": "2026-09-19T00:20:00Z"},
            {"type": "DiskPressure", "status": "False"},
        ]}}
        spec = {"reliability": {"disruptionRisk": 20, "minimumReadySeconds": 1800, "maxNotReadyEvents": 0}}
        result = controller.node_reliability(node, [], spec, datetime(2026, 9, 19, 0, 30, tzinfo=timezone.utc))
        self.assertFalse(result["eligible"])
        self.assertEqual(result["readySeconds"], 600)

    def test_reliability_quarantines_recent_not_ready(self):
        node = {"metadata": {"name": "worker"}, "spec": {}, "status": {"conditions": [
            {"type": "Ready", "status": "True", "lastTransitionTime": "2026-09-18T20:00:00Z"},
        ]}}
        events = [{"involvedObject": {"name": "worker"}, "reason": "NodeNotReady", "lastTimestamp": "2026-09-19T00:00:00Z"}]
        spec = {"reliability": {"disruptionRisk": 20, "minimumReadySeconds": 1800, "maxNotReadyEvents": 0}}
        result = controller.node_reliability(node, events, spec, datetime(2026, 9, 19, 0, 30, tzinfo=timezone.utc))
        self.assertFalse(result["eligible"])
        self.assertEqual(result["recentNotReady"], 1)

    def test_reliability_accepts_compatible_stable_node(self):
        node = {"metadata": {"name": "worker"}, "spec": {}, "status": {"conditions": [
            {"type": "Ready", "status": "True", "lastTransitionTime": "2026-09-18T20:00:00Z"},
            {"type": "DiskPressure", "status": "False"},
        ]}}
        spec = {"reliability": {"disruptionRisk": 20, "minimumReadySeconds": 1800, "maxNotReadyEvents": 0}}
        result = controller.node_reliability(node, [], spec, datetime(2026, 9, 19, 0, 30, tzinfo=timezone.utc))
        self.assertTrue(result["eligible"])
        self.assertEqual(result["score"], 80)

    def test_serving_labels_fail_closed_with_node_reliability(self):
        spec = {"capabilities": {"storage": ["ceph-client"], "gpu": "rocm-wsl", "egress": True,
                                  "publicEdge": True, "functionExecution": True}}
        healthy = controller.serving_labels(spec, {"eligible": True})
        self.assertTrue(all(healthy[key] == "true" for key in controller.SERVING_LABELS))
        unhealthy = controller.serving_labels(spec, {"eligible": False})
        self.assertTrue(all(unhealthy[key] == "false" for key in controller.SERVING_LABELS))

    @patch.object(controller.urllib.request, "urlopen", side_effect=OSError("metrics unavailable"))
    def test_trimaran_outage_does_not_block_node_projection(self, _urlopen):
        self.assertEqual(controller.trimaran_nodes(), set())

if __name__ == "__main__": unittest.main()
