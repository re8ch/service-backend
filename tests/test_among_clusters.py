from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
HEADLAMP = ROOT / "platform/headlamp"


def test_among_clusters_bundle_is_checksum_pinned_and_contains_page():
    deployment = (HEADLAMP / "deployment.yaml").read_text()
    kustomization = (HEADLAMP / "kustomization.yaml").read_text()
    assert "re8ch-headlamp-among-clusters-0.3.24.bundle" in kustomization
    assert "47d9658c3e8596e99a13dd86c9dd273078917176fe4529d35467d18d6c59ce4f" in deployment
    assert "among-clusters-onboarding-links.yaml" in kustomization
    assert "headlamp-advanced-fabric-0.23.0.bundle.b64" in kustomization
    assert "7af17af58c4927fc63a40ad070209049d290e096dd4f4eb662ba705e6b029cd9" in deployment
    assert "headlamp-advanced-fabric-v0230" in kustomization
    assert "values: [domain-ex]" in deployment
    assert "github.com/re8ch/" not in deployment
    assert not re.search(r"client-certificate-data|client-key-data|Bearer\\s+[A-Za-z0-9]", deployment, re.I)
