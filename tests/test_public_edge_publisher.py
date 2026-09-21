from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_public_edge_publisher_uses_available_authority_node():
    documents = list(yaml.safe_load_all((ROOT / "platform/public-edge-manager/release.yaml").read_text()))
    release = next(item for item in documents if item.get("kind") == "HelmRelease")
    values = release["spec"]["values"]
    publisher = values["publication"]["publisherNode"]
    assert publisher == "restricted-node-296f36f2fb"
    assert values["authorityNodes"][publisher] == {
        "bindAddress": "192.0.2.208",
        "area": "CN",
        "region": "loudi",
    }
    assert "overseas-kl" not in values["authorityNodes"]
    assert "vm-20-12-ubuntu" not in values["authorityNodes"]
    assert values["fabricEvidence"]["mode"] == "Required"
    assert values["fabricEvidence"]["requireNodeReady"] is True
