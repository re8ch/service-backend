from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_public_edge_publisher_is_a_live_off_lan_authority():
    documents = list(yaml.safe_load_all((ROOT / "platform/public-edge-manager/release.yaml").read_text()))
    release = next(item for item in documents if item.get("kind") == "HelmRelease")
    values = release["spec"]["values"]
    publisher = values["publication"]["publisherNode"]
    assert publisher == "overseas-kl"
    assert values["authorityNodes"][publisher] == {
        "bindAddress": "192.0.2.245",
        "area": "APAC",
        "region": "my-kul",
    }
    assert values["fabricEvidence"]["mode"] == "Required"
    assert values["fabricEvidence"]["requireNodeReady"] is True
