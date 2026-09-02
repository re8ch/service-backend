import importlib.util
import json
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_r640_public_authority_gate_passes():
    path = ROOT / "operations/scripts/validate-r640-public-authority.py"
    spec = importlib.util.spec_from_file_location("validate_r640_public_authority", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.validate()


def test_r640_public_authority_gate_rejects_zerotier_dummy(tmp_path):
    path = ROOT / "operations/scripts/validate-r640-public-authority.py"
    spec = importlib.util.spec_from_file_location("validate_r640_public_authority_bad", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    inventory = tmp_path / "inventory.json"
    inventory.write_text(module.INVENTORY.read_text())
    documents = list(yaml.safe_load_all(module.AMONG_RELEASE.read_text()))
    release = next(item for item in documents if item and item.get("kind") == "HelmRelease" and item["metadata"]["name"] == "among-clusters-agent")
    release["spec"]["values"]["gateway"]["listener"]["publicEndpoint"] = "quic://192.0.2.183:8443"
    rendered = tmp_path / "release.yaml"
    rendered.write_text("---\n".join(yaml.safe_dump(item, sort_keys=False) for item in documents))
    with pytest.raises(SystemExit, match="not ZeroTier/dummy"):
        module.validate(inventory, rendered)


def test_platform_memories_receive_public_authority_context():
    for memory in (ROOT / "catalogs/platform/modules").glob("*/MEMORY.md"):
        text = memory.read_text()
        assert "## restricted-node-6dc3f140f9 public network authority" in text
        assert "routing/service identities, not directly attached public endpoints" in text


def test_re8ch_peer_links_use_provider_public_authority():
    links = yaml.safe_load_all((ROOT / "platform/among-clusters/byoc-pilot.yaml").read_text())
    for item in links:
        if item and item.get("kind") == "AuthenticatedLink" and item["spec"]["peerRef"].startswith("re8ch-"):
            assert item["spec"]["localGateway"] == "quic://192.0.2.79:8443"
            assert "remoteGateway" not in item["spec"]
