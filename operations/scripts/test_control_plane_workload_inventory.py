#!/usr/bin/env python3
import importlib.util
import unittest
from pathlib import Path

import yaml


MODULE_PATH = Path(__file__).with_name("control-plane-workload-inventory.py")
SPEC = importlib.util.spec_from_file_location("control_plane_workload_inventory", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def workload(namespace, kind="Deployment", labels=None):
    return {
        "apiVersion": "apps/v1",
        "kind": kind,
        "metadata": {"name": "example", "namespace": namespace, "labels": labels or {}},
        "spec": {"template": {"metadata": {"labels": labels or {}}, "spec": {}}},
    }


def pod(namespace, labels=None):
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "example-pod", "namespace": namespace, "labels": labels or {}},
        "spec": {},
    }


class ClassificationTests(unittest.TestCase):
    def test_observability_deployment_requires_explicit_placement(self):
        self.assertEqual(
            MODULE.classify(pod("observability-system"), workload("observability-system")),
            "unmanaged",
        )

    def test_node_daemon_remains_allowed(self):
        self.assertEqual(
            MODULE.classify(
                pod("observability-system"),
                workload("observability-system", kind="DaemonSet"),
            ),
            "daemon",
        )

    def test_explicit_allow_label_remains_allowed(self):
        labels = {MODULE.ALLOW_LABEL: "true"}
        self.assertEqual(
            MODULE.classify(pod("scheduling-system", labels), workload("scheduling-system", labels=labels)),
            "approved",
        )


class ControlPlaneReservationTests(unittest.TestCase):
    def test_wsl_host_configs_match_control_small_profile(self):
        root = Path(__file__).resolve().parents[2]
        profile_document = next(
            yaml.safe_load_all(
                (root / "platform/scheduling-fabric/resources/node-profiles.yaml").read_text()
            )
        )
        profiles = yaml.safe_load(profile_document["data"]["profiles.yaml"])
        control_small = profiles["control-small"]
        expected_args = {
            f"max-pods={control_small['maxPods']}",
            "kube-reserved=cpu=2,memory=2Gi,ephemeral-storage=2Gi,pid=2048",
            "system-reserved=cpu=1,memory=2Gi,ephemeral-storage=2Gi,pid=2048",
            "enforce-node-allocatable=pods",
            "eviction-hard=memory.available<2Gi,nodefs.available<10%,imagefs.available<15%,pid.available<10%",
        }
        for node in ("a1", "b1"):
            config = yaml.safe_load(
                (root / f"clusters/re8ch/components/ops/{node}-wsl2-k3s-server-config.yaml").read_text()
            )
            self.assertEqual(set(config["kubelet-arg"]), expected_args)
            self.assertEqual(
                config["node-taint"],
                ["node-role.kubernetes.io/control-plane=true:NoSchedule"],
            )


if __name__ == "__main__":
    unittest.main()
