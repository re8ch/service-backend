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
    assert len(rendered) == 15
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
    assert first["nodes"]["restricted-node-abb3f27a69"]["applyEligible"] is True
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


def test_transit_receive_contract_covers_both_routeros_and_linux():
    outputs = FABRIC.validate(FABRIC.load_model())
    contract = outputs[FABRIC.HOST_CONTRACT]["transitPaths"]
    policy = outputs["operations/network/manifests/network/private-transit/routeros-control-mesh.json"]["podRoutePolicy"]
    router = outputs["clusters/re8ch/components/network/host-network-controller/routeros-transit.json"]
    assert contract["sources"] == policy["transitSources"] == router["sources"]
    assert {"192.0.2.254/24", "192.0.2.184/24"} <= set(contract["linuxReceivers"]["restricted-node-abb3f27a69"]["transitSources"])
    assert all(len(path["forward"]) == len(set(path["forward"])) for path in contract["paths"])
    assert any(path["sourceNode"] == "overseas-flk" and path["destinationNode"] == "restricted-node-abb3f27a69"
               for path in contract["paths"])


def test_transit_duplicate_pod_ownership_rejected():
    model = copy.deepcopy(FABRIC.load_model())
    nodes = model["sections"]["podcidrFabric"]["nodes"]
    nodes[1]["podCIDR"] = nodes[0]["podCIDR"]
    with pytest.raises(ValueError, match="overlapping transit source ownership"):
        FABRIC.transit_contract(model)


def test_relay_return_forwarding_is_exact_and_bidirectional():
    outputs = FABRIC.validate(FABRIC.load_model())
    rules = outputs["clusters/re8ch/components/network/podcidr-fabric/inventory.json"]["transitForwarding"]["restricted-node-6dc3f140f9"]
    assert {"source": "192.0.2.214/24", "destination": "192.0.2.254/24",
            "inInterface": "wg-pt-mubao", "outInterface": "wg-pt-b1"} in rules
    for source in ("192.0.2.114/32", "192.0.2.220/32"):
        assert {"source": source, "destination": "192.0.2.254/24",
                "inInterface": "wg-pt-mubao", "outInterface": "wg-pt-b1"} in rules
    for rule in rules:
        assert {"source": rule["destination"], "destination": rule["source"],
                "inInterface": rule["outInterface"], "outInterface": rule["inInterface"]} in rules
        assert rule["source"].startswith(("10.42.", "10.250."))
        assert rule["destination"].startswith(("10.42.", "10.250."))
        assert "eth" not in rule["inInterface"] + rule["outInterface"]


def test_host_sources_derive_only_declared_identity_and_remote_pod_destinations():
    model = FABRIC.load_model()
    output = FABRIC.validate(model)["clusters/re8ch/components/network/podcidr-fabric/inventory.json"]
    policies = output["hostSourceIdentity"]
    assert set(policies) == {"overseas-flk", "overseas-la-2"}
    assert policies["overseas-flk"]["source"] == "192.0.2.109"
    assert "192.0.2.214/24" in policies["overseas-flk"]["destinations"]
    assert "192.0.2.254/24" not in policies["overseas-flk"]["destinations"]
    assert all(p.startswith("10.42.") for p in policies["overseas-flk"]["destinations"])
    assert policies["overseas-flk"]["prefixMatches"] == [
        "192.0.2.224/16 ge 24 le 24", "192.0.2.106/24 ge 32 le 32", "192.0.2.215/24 ge 32 le 32"]
    model["routeIntent"]["hostSourceIdentity"]["source"] = "publicIp"
    with pytest.raises(ValueError, match="source identity policy"):
        FABRIC.validate(model)
