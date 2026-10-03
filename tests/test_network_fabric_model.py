import copy
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "operations/network/fabric/fabric_model.py"
SPEC = importlib.util.spec_from_file_location("fabric_model", SCRIPT)
FABRIC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FABRIC)


def test_all_consumers_match_single_fabric_model():
    rendered = FABRIC.validate(FABRIC.load_model())
    assert len(rendered) == 10
    for relative, expected in rendered.items():
        assert json.loads((ROOT / relative).read_text()) == expected


def test_cross_fabric_identity_drift_is_rejected():
    model = copy.deepcopy(FABRIC.load_model())
    node = next(node for node in model["sections"]["podcidrFabric"]["nodes"]
                if node["name"] == "overseas-la-2")
    node["acceleratedIp"] = "192.0.2.90"
    with pytest.raises(ValueError, match="conflicting accelerated identity"):
        FABRIC.validate(model)


def test_unreviewed_public_authority_and_mesh_adjacency_are_rejected():
    model = copy.deepcopy(FABRIC.load_model())
    public = model["sections"]["publicMesh"]["nodes"]
    restricted-node-6dc3f140f9 = next(node for node in public if node["name"] == "restricted-node-6dc3f140f9")
    restricted-node-6dc3f140f9["publicAuthority"]["gatewayVIP"] = "192.0.2.59"
    with pytest.raises(ValueError, match="public Gateway authority"):
        FABRIC.validate(model)
    restricted-node-6dc3f140f9["publicAuthority"]["gatewayVIP"] = "192.0.2.209"
    mubao = next(node for node in public if node["name"] == "restricted-node-abb3f27a69")
    mubao["meshPeers"].remove("overseas-flk")
    with pytest.raises(ValueError, match="cannot dial public spine"):
        FABRIC.validate(model)


def test_host_contract_uses_runtime_node_names_and_has_stable_revision():
    model = FABRIC.load_model()
    first = FABRIC.host_contract(model)
    assert first == FABRIC.host_contract(copy.deepcopy(model))
    assert "suxunji" in first["nodes"]
    assert "artchais-nuclio-1" not in first["nodes"]
    assert first["nodes"]["restricted-node-abb3f27a69"]["applyEligible"] is False
    assert {item["name"] for item in first["nodes"]["restricted-node-abb3f27a69"]["wireguard"]} == {
        "wg-public-mesh", "wg-cp-mubao", "wg-pt-mubao"}
    changed = copy.deepcopy(model)
    changed["hostNetwork"]["rollout"]["verificationTimeoutSeconds"] += 1
    assert FABRIC.host_contract(changed)["modelDigest"] != first["modelDigest"]


@pytest.mark.parametrize("mutation,match", [
    (lambda c: c.update(adoptedNodes=["restricted-node-6dc3f140f9"]), "adoption controller"),
    (lambda c: c["ownership"]["frr"].update(wholeFileReplacement=True), "wholesale"),
    (lambda c: c["rollback"].update(requiresClusterApi=True), "without the cluster API"),
    (lambda c: c["rollout"].update(maxUnavailable=2), "serial"),
])
def test_host_contract_rejects_unsafe_activation(mutation, match):
    model = copy.deepcopy(FABRIC.load_model())
    mutation(model["hostNetwork"])
    with pytest.raises(ValueError, match=match):
        FABRIC.validate(model)
