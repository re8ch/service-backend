from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_public_edge_discovers_nodes_without_inventory_or_publisher():
    documents = list(yaml.safe_load_all((ROOT / "platform/public-edge-manager/release.yaml").read_text()))
    release = next(item for item in documents if item.get("kind") == "HelmRelease")
    values = release["spec"]["values"]
    assert "authorityNodes" not in values
    assert "publicEdges" not in values
    assert "publisherNode" not in values["publication"]
    assert values["publication"]["enabled"] is False
    discovery = values["discovery"]
    assert discovery["replicas"] == 2
    assert discovery["gatewaySelector"] == "networking.re8ch.com/public-edge-gateway=true"
    assert discovery["maxNameservers"] == 3
    assert not (ROOT / "platform/public-edge-manager/dns-candidate-nodes.yaml").exists()
    assert not (ROOT / "platform/public-edge-manager/nameserver-election.yaml").exists()
    assert values["fabricEvidence"]["mode"] == "Required"
    assert values["fabricEvidence"]["requireNodeReady"] is False


def test_functions_zone_is_delegation_ready_and_access_stays_on_subaccess():
    documents = list(yaml.safe_load_all((ROOT / "platform/public-edge-manager/release.yaml").read_text()))
    release = next(item for item in documents if item.get("kind") == "HelmRelease")
    dns = release["spec"]["values"]["dns"]
    assert "functions.re8ch.com." in dns["zones"]
    functions = {name: records for name, records in dns["externalRecords"].items()
                 if name.endswith(".functions.re8ch.com.")}
    assert len(functions) == 38
    assert all(len(records) == 1 and records[0]["type"] == "CNAME"
               and records[0]["externalCDN"] for records in functions.values())
    assert not (ROOT / "clusters/re8ch/components/netbird-access/ingress.yaml").exists()
    personal_access = (ROOT / "clusters/re8ch/components/personal-access/data-plane.yaml").read_text()
    assert "external-dns.alpha.kubernetes.io/hostname: access.re8ch.com" in personal_access
    assert "sub.access.re8ch.com" in personal_access
