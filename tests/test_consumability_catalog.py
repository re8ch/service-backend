import json
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
CATALOG = ROOT / "inventory/platform/consumability/catalog.yaml"
SNAPSHOT = ROOT / "inventory/platform/live/services.yaml"


def test_every_live_namespace_has_exactly_one_consumability_assessment():
    catalog = yaml.safe_load(CATALOG.read_text())
    snapshot = json.loads(SNAPSHOT.read_text())
    live = {resource["namespace"] for resource in snapshot["resources"]}
    classified = []
    for assessment in catalog["spec"]["assessmentClasses"].values():
        classified.extend(assessment["namespaces"])

    assert len(classified) == len(set(classified)), "assessment classes overlap"
    assert live == set(classified)


def test_consumable_service_classes_have_stable_meter_contracts():
    catalog = yaml.safe_load(CATALOG.read_text())
    classes = catalog["spec"]["serviceClasses"]
    ids = [item["id"] for item in classes]
    assert len(ids) == len(set(ids))
    assert "network.among-clusters.v1" in ids
    assert "registry.harbor.v1" in ids
    assert "observability.managed.v1" in ids
    for item in classes:
        assert item["meters"]
        assert item["source"]
        assert item["allocation"]


def test_admin_and_consumer_plugin_identities_are_disjoint():
    contract = yaml.safe_load(CATALOG.read_text())["spec"]["identityContract"]
    assert set(contract["administrativePlugins"]).isdisjoint(contract["consumerPlugins"])
    assert {"tenant_ref", "service_instance_ref", "resource_ref"} <= set(contract["requiredDimensions"])
