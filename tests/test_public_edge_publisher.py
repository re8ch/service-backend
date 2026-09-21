from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_public_edge_publisher_uses_available_authority_node():
    documents = list(yaml.safe_load_all((ROOT / "platform/public-edge-manager/release.yaml").read_text()))
    release = next(item for item in documents if item.get("kind") == "HelmRelease")
    values = release["spec"]["values"]
    publisher = values["publication"]["publisherNode"]
    assert publisher == "suxunji"
    assert values["authorityNodes"][publisher] == {
        "bindAddress": "192.0.2.83",
        "area": "CN",
        "region": "shanghai",
    }
    assert set(values["authorityNodes"]) == {"domain-ex", "suxunji"}
    admitted = list(yaml.safe_load_all((ROOT / "platform/public-edge-manager/dns-candidate-nodes.yaml").read_text()))
    assert {item["metadata"]["name"] for item in admitted} == set(values["authorityNodes"])
    assert "overseas-kl" not in values["authorityNodes"]
    assert "vm-20-12-ubuntu" not in values["authorityNodes"]
    assert values["fabricEvidence"]["mode"] == "Required"
    assert values["fabricEvidence"]["requireNodeReady"] is True
