from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_paralus_retirement_is_bounded_to_one_helmrelease():
    objects = list(
        yaml.safe_load_all(
            (ROOT / "clusters/re8ch/components/ops/paralus-helmrelease-retirement.yaml").read_text()
        )
    )
    role = next(item for item in objects if item["kind"] == "Role")
    job = next(item for item in objects if item["kind"] == "Job")

    assert role["metadata"] == {
        "name": "paralus-helmrelease-retirement",
        "namespace": "flux-system",
    }
    assert role["rules"] == [
        {
            "apiGroups": ["helm.toolkit.fluxcd.io"],
            "resources": ["helmreleases"],
            "resourceNames": ["paralus"],
            "verbs": ["get", "delete"],
        }
    ]

    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["image"].startswith("curlimages/curl@sha256:")
    script = container["args"][0]
    assert "/namespaces/flux-system/helmreleases/paralus" in script
    assert "--request DELETE" in script
