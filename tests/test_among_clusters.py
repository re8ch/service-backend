from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
HEADLAMP = ROOT / "platform/headlamp"


def test_among_clusters_bundle_is_checksum_pinned_and_contains_page():
    deployment = (HEADLAMP / "deployment.yaml").read_text()
    kustomization = (HEADLAMP / "kustomization.yaml").read_text()
    assert "re8ch-headlamp-among-clusters-0.2.0.bundle.b64" in kustomization
    assert "a155e0e9717b040ac07260ff8ad49ba7223f48ca13806bf3961d20f1f3f283be" in deployment
    assert "re8ch-headlamp-advanced-fabric-0.12.0.bundle.b64" in kustomization
    assert "da561b967d0a08fe90a5430893643080a0c2f439409f0bf26017efa9ff674d9c" in deployment
    assert "github.com/re8ch/" not in deployment
    assert not re.search(r"client-certificate-data|client-key-data|Bearer\\s+[A-Za-z0-9]", deployment, re.I)
