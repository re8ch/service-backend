import hashlib
import io
from pathlib import Path
import re
import tarfile

import yaml


ROOT = Path(__file__).resolve().parents[1]
HEADLAMP = ROOT / "platform/headlamp"


def test_among_clusters_bundle_is_checksum_pinned_and_contains_page():
    deployment = (HEADLAMP / "deployment.yaml").read_text()
    kustomization = yaml.safe_load((HEADLAMP / "kustomization.yaml").read_text())
    generator = next(item for item in kustomization["configMapGenerator"]
                     if item["name"] == "headlamp-penrose-plugin-v0110")
    bundle_name = generator["files"][0].split("=", 1)[1].removeprefix("assets/")
    bundle = HEADLAMP / "assets" / bundle_name
    checksum = hashlib.sha256(bundle.read_bytes()).hexdigest()
    assert f"echo '{checksum}  /tmp/plugin.tar.gz'" in deployment
    with tarfile.open(fileobj=io.BytesIO(bundle.read_bytes()), mode="r:gz") as archive:
        main = next(member for member in archive.getmembers() if member.name.endswith("/main.js"))
        javascript = archive.extractfile(main).read().decode()
    assert "AmongClusters" in javascript
    assert "collaborationclusters" in javascript
    assert "collaborationrelationships" in javascript
    assert "collaborationevents" in javascript
    assert "among-clusters-catalog" not in javascript
    assert "Explicitly published services" in javascript
    assert not re.search(r"client-certificate-data|client-key-data|Bearer\\s+[A-Za-z0-9]", javascript, re.I)
