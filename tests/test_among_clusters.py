from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
HEADLAMP = ROOT / "platform/headlamp"


def test_among_clusters_bundle_is_checksum_pinned_and_contains_page():
    deployment = (HEADLAMP / "deployment.yaml").read_text()
    kustomization = (HEADLAMP / "kustomization.yaml").read_text()
    assert "re8ch-headlamp-among-clusters-0.3.6.bundle" in kustomization
    assert "aee9c0fbc3f16d200c866cb8ca41394d5c7070e51b3baa9a27bcb6284dbadcf1" in deployment
    assert "re8ch-headlamp-advanced-fabric-0.15.0.bundle.b64" in kustomization
    assert "106b15090df0b4b770a4dd4379cbb404711f7a8d9111fad1d8c6d7bb5bc94185" in deployment
    assert "github.com/re8ch/" not in deployment
    assert not re.search(r"client-certificate-data|client-key-data|Bearer\\s+[A-Za-z0-9]", deployment, re.I)
