from pathlib import Path
import re
import yaml


ROOT = Path(__file__).resolve().parents[1]
HEADLAMP = ROOT / "platform/headlamp"


def test_among_clusters_bundle_is_checksum_pinned_and_contains_page():
    deployment = (HEADLAMP / "deployment.yaml").read_text()
    kustomization = (HEADLAMP / "kustomization.yaml").read_text()
    assert "re8ch-headlamp-among-clusters-0.3.24.bundle" in kustomization
    assert "47d9658c3e8596e99a13dd86c9dd273078917176fe4529d35467d18d6c59ce4f" in deployment
    assert "among-clusters-onboarding-links.yaml" in kustomization
    assert "headlamp-advanced-fabric-0.26.1.bundle.b64" in kustomization
    assert "f2277c0899822f6b3d0a86e9c0443e9abb40e2ce62f62d3141cc271f60e4b74e" in deployment
    assert "headlamp-advanced-fabric-v0261" in kustomization
    assert "scheduling.re8ch.com/placement-class: control-plane-critical" in deployment
    assert 'node-role.kubernetes.io/control-plane: "true"' in deployment
    # Bundled plugins must remain self-contained; separately published plugins
    # may download their immutable, checksum-verified release packages.
    installers = yaml.safe_load(deployment)["spec"]["template"]["spec"]["initContainers"]
    for installer in installers:
        if installer["name"] in {"install-among-clusters-plugin", "install-advanced-fabric-plugin"}:
            assert "github.com/re8ch/" not in yaml.safe_dump(installer)
    assert not re.search(r"client-certificate-data|client-key-data|Bearer\\s+[A-Za-z0-9]", deployment, re.I)
