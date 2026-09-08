from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
HEADLAMP = ROOT / "platform/headlamp"


def test_among_clusters_bundle_is_checksum_pinned_and_contains_page():
    deployment = (HEADLAMP / "deployment.yaml").read_text()
    kustomization = (HEADLAMP / "kustomization.yaml").read_text()
    assert "re8ch-headlamp-among-clusters-0.3.6.bundle" in kustomization
    assert "aee9c0fbc3f16d200c866cb8ca41394d5c7070e51b3baa9a27bcb6284dbadcf1" in deployment
    assert "re8ch-headlamp-advanced-fabric-0.14.0.bundle.b64" in kustomization
    assert "cfc12a034a31d47f27384ea7a5d375fa2c3864f6a56d066adb1fc1f2bb44c71e" in deployment
    assert "github.com/re8ch/" not in deployment
    assert not re.search(r"client-certificate-data|client-key-data|Bearer\\s+[A-Za-z0-9]", deployment, re.I)
