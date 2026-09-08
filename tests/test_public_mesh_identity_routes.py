import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_r640_and_vm_delegate_each_others_identity_to_public_mesh():
    inventory = json.loads(
        (ROOT / "clusters/re8ch/components/network/podcidr-fabric/inventory.json").read_text()
    )
    nodes = {node["name"]: node for node in inventory["nodes"]}

    assert nodes["restricted-node-6dc3f140f9"]["publicMeshIdentityPeers"] == ["vm-20-12-ubuntu"]
    assert nodes["vm-20-12-ubuntu"]["publicMeshIdentityPeers"] == ["restricted-node-6dc3f140f9"]
    assert nodes["restricted-node-6dc3f140f9"]["publicMeshPodCIDRPeers"] == ["vm-20-12-ubuntu"]
    assert nodes["vm-20-12-ubuntu"]["publicMeshPodCIDRPeers"] == ["restricted-node-6dc3f140f9"]


def test_reconciler_removes_identity_interface_route_for_public_mesh_peer():
    reconciler = (
        ROOT / "clusters/re8ch/components/network/podcidr-fabric/reconciler.yaml"
    ).read_text()

    assert reconciler.count("publicMeshIdentityPeers") >= 2
    assert 'no ip route ${peer_ip}/32 ${identity_iface} 5' in reconciler
    assert 'no ip route ${api_ip}/32 ${identity_iface} 5' in reconciler
    assert 'no ip route ${peer_pods} wg-pt-a1 5' in reconciler
