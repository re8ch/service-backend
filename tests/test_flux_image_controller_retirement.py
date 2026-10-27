from pathlib import Path

import yaml

# LibYAML keeps SafeLoader constructors; pure Python remains a supported fallback.
YAML_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

ROOT = Path(__file__).resolve().parents[1]


def test_unused_image_controllers_are_not_rendered(kustomize_documents):
    objects = kustomize_documents("clusters/re8ch", kubectl=True)
    deployments = {
        item["metadata"]["name"]: item
        for item in objects
        if item and item.get("kind") == "Deployment" and item.get("metadata", {}).get("namespace") == "flux-system"
    }
    for name in ("image-reflector-controller", "image-automation-controller"):
        assert name not in deployments


def test_declared_capability_inventory_marks_image_controllers_absent():
    capabilities = yaml.load(
        (ROOT / "operations/flux-lightweight/controller-capabilities.yaml").read_text(), Loader=YAML_SAFE_LOADER
    )["spec"]["controllers"]
    assert capabilities["image-reflector-controller"]["disposition"] == "keep-absent"
    assert capabilities["image-automation-controller"]["disposition"] == "keep-absent"
