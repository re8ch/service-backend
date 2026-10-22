import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_unused_image_controllers_render_at_zero_replicas():
    rendered = subprocess.check_output(["kubectl", "kustomize", "clusters/re8ch"], cwd=ROOT)
    objects = list(yaml.safe_load_all(rendered))
    deployments = {
        item["metadata"]["name"]: item
        for item in objects
        if item and item.get("kind") == "Deployment" and item.get("metadata", {}).get("namespace") == "flux-system"
    }
    for name in ("image-reflector-controller", "image-automation-controller"):
        assert deployments[name]["spec"]["replicas"] == 0


def test_declared_capability_inventory_marks_image_controllers_absent():
    capabilities = yaml.safe_load(
        (ROOT / "operations/flux-lightweight/controller-capabilities.yaml").read_text()
    )["spec"]["controllers"]
    assert capabilities["image-reflector-controller"]["disposition"] == "keep-absent"
    assert capabilities["image-automation-controller"]["disposition"] == "keep-absent"
