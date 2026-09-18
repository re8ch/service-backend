import importlib.util
from pathlib import Path
import unittest

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

    def test_quantity_parsers(self):
        self.assertAlmostEqual(controller.cpu("750m"),.75)
        self.assertEqual(controller.memory("2Gi"),2*1024**3)

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

if __name__ == "__main__": unittest.main()
