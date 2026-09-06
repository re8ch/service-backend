from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "platform/connectool/toolhive-operator-release.yaml"


def objects():
    return [item for item in yaml.safe_load_all(RELEASE.read_text()) if item]


def test_toolhive_operator_charts_are_exactly_pinned():
    sources = {item["metadata"]["name"]: item for item in objects() if item["kind"] == "OCIRepository"}
    assert sources["toolhive-operator"]["spec"]["url"] == "oci://ghcr.io/stacklok/toolhive/toolhive-operator"
    assert sources["toolhive-operator-crds"]["spec"]["url"] == "oci://ghcr.io/stacklok/toolhive/toolhive-operator-crds"
    assert {source["spec"]["ref"]["tag"] for source in sources.values()} == {"0.41.0"}


def test_operator_adopts_existing_releases_without_renaming_them():
    releases = {item["metadata"]["name"]: item for item in objects() if item["kind"] == "HelmRelease"}
    assert releases["toolhive-operator"]["spec"]["releaseName"] == "toolhive-operator"
    assert releases["toolhive-operator"]["spec"]["storageNamespace"] == "toolhive-system"
    assert releases["toolhive-operator-crds"]["spec"]["releaseName"] == "toolhive-crds"
    assert releases["toolhive-operator-crds"]["spec"]["storageNamespace"] == "default"
    assert releases["toolhive-operator"]["spec"]["dependsOn"] == [
        {"name": "toolhive-operator-crds", "namespace": "flux-system"}
    ]


def test_operator_values_match_the_production_ha_placement():
    release = next(
        item for item in objects() if item["kind"] == "HelmRelease" and item["metadata"]["name"] == "toolhive-operator"
    )
    operator = release["spec"]["values"]["operator"]
    assert operator["replicaCount"] == 2
    assert operator["rbac"] == {"scope": "namespace", "allowedNamespaces": ["toolhive-system"]}
    expression = operator["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][0]
    assert expression["values"] == ["restricted-node-1eb0570f74", "restricted-node-296f36f2fb"]
