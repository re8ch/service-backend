from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
OPERATIONAL = ROOT / "operations/network/manifests/cilium/multi-pool-values.yaml"
COMPONENT = ROOT / "clusters/re8ch/components/cilium/multi-pool-values.yaml"


def test_multi_pool_overlay_keeps_physical_egress_masqueraded():
    operational = yaml.safe_load(OPERATIONAL.read_text())
    component = yaml.safe_load(COMPONENT.read_text())

    assert operational == component
    assert component["ipam"] == {
        "mode": "multi-pool",
        "multiPoolPreAllocation": "tenant-apps=8",
    }
    assert component["extraConfig"]["ipam-default-ip-pool"] == "tenant-apps"
    assert component["k8s"]["requireIPv4PodCIDR"] is False
    assert component["ipv4NativeRoutingCIDR"] == "192.0.2.183/8"
    assert component["bpf"]["masquerade"] is False
    assert component["egressMasqueradeInterfaces"] == "eth0,eno2np1"
    assert component["rollOutCiliumPods"] is True
