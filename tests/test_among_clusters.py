import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

import yaml


ROOT = Path(__file__).resolve().parents[1]
HEADLAMP = ROOT / "platform/headlamp"


def test_among_clusters_catalog_matches_managed_headlamp_contexts():
    catalog_map = yaml.safe_load((HEADLAMP / "among-clusters-catalog.yaml").read_text())
    catalog = json.loads(catalog_map["data"]["catalog.json"])
    contexts = {cluster["context"] for cluster in catalog["clusters"]}
    assert contexts == {"re8ch-k3s", "artc", "qwen"}
    assert catalog["refreshSeconds"] >= 10
    for cluster in catalog["clusters"]:
        assert cluster["owner"]
        assert cluster["relationship"]
        for service in cluster.get("sharedServices", []):
            assert service["direction"] in {
                "owner-to-consumer", "consumer-to-owner", "bidirectional"
            }
            assert service["access"]


def test_among_clusters_bundle_is_checksum_pinned_and_contains_page():
    deployment = (HEADLAMP / "deployment.yaml").read_text()
    kustomization = yaml.safe_load((HEADLAMP / "kustomization.yaml").read_text())
    generator = next(item for item in kustomization["configMapGenerator"]
                     if item["name"] == "headlamp-penrose-plugin-v0101")
    bundle_name = generator["files"][0].split("=", 1)[1].removeprefix("assets/")
    bundle = HEADLAMP / "assets" / bundle_name
    checksum = hashlib.sha256(bundle.read_bytes()).hexdigest()
    assert f"echo '{checksum}  /tmp/plugin.tar.gz'" in deployment
    with tarfile.open(fileobj=io.BytesIO(bundle.read_bytes()), mode="r:gz") as archive:
        main = next(member for member in archive.getmembers() if member.name.endswith("/main.js"))
        javascript = archive.extractfile(main).read().decode()
    assert "AmongClusters" in javascript
    assert "among-clusters-catalog" in javascript
    assert "Explicitly published services" in javascript
    assert not re.search(r"client-certificate-data|client-key-data|Bearer\\s+[A-Za-z0-9]", javascript, re.I)
