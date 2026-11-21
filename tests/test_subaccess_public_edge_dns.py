from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_subaccess_parent_record_delegates_candidate_selection_to_public_edge():
    resources = [item for item in yaml.safe_load_all(
        (ROOT / "clusters/re8ch/components/personal-access/data-plane.yaml").read_text()
    ) if item]
    ingresses = {
        item["metadata"]["name"]: item
        for item in resources
        if item.get("kind") == "Ingress"
    }

    portal = ingresses["access-subscription-public-edge-dns"]
    annotations = portal["metadata"]["annotations"]
    assert annotations["external-dns.alpha.kubernetes.io/hostname"] == "sub.access.re8ch.com"
    assert annotations["external-dns.alpha.kubernetes.io/target"] == "sub-access.service.re8ch.com"

    legacy = ingresses["access-hysteria2-dns"]["metadata"]["annotations"]
    assert "sub.access.re8ch.com" not in legacy["external-dns.alpha.kubernetes.io/hostname"]
