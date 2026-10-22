from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_unified_release_publishes_and_verifies_exact_oci_digest():
    workflow = (ROOT / ".github/workflows/unified-release.yml").read_text()
    assert "workflow_dispatch:" in workflow
    assert "flux push artifact" in workflow
    assert ".repository + \"@\" + .digest" in workflow
    assert "cosign sign --yes \"$immutable\"" in workflow
    assert "unified-release.yml@refs/heads/main" in workflow
    assert "payloadDigest" in workflow
    assert "kubectl apply" not in workflow


def test_canary_contract_is_exactly_namespaced_and_non_pruning():
    contract = yaml.safe_load((ROOT / "contracts/releases/api-conformance-runner-projection.yaml").read_text())
    assert contract["spec"]["target"]["namespace"] == "api-conformance-runners"
    assert contract["spec"]["authorization"]["resources"] == ["core/ConfigMap"]
    assert contract["spec"]["authorization"]["objectNames"] == ["api-conformance-runner-projection-contract"]
    assert contract["spec"]["lifecycle"]["prune"] is False
