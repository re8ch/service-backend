import importlib.util
from functools import lru_cache
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "operations/flux-lightweight/inventory.py"
SPEC = importlib.util.spec_from_file_location("flux_lightweight_inventory", MODULE_PATH)
INVENTORY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INVENTORY)


@lru_cache(maxsize=None)
def inventory(*roots: str):
    return INVENTORY.inventory([ROOT / root for root in roots])


def test_every_catalog_kind_has_one_controller():
    kinds, controllers = INVENTORY.load_capabilities()
    expected = sum(len(item["apiKinds"]) for item in controllers.values())
    assert len(kinds) == expected
    assert kinds[("source.toolkit.fluxcd.io", "GitRepository")] == "source-controller"
    assert kinds[("kustomize.toolkit.fluxcd.io", "Kustomization")] == "kustomize-controller"
    assert kinds[("helm.toolkit.fluxcd.io", "HelmRelease")] == "helm-controller"
    for controller in controllers.values():
        assert controller["upstreamVersion"]
        assert controller["sourceReferences"]
        assert all(reference.startswith("https://github.com/fluxcd/") for reference in controller["sourceReferences"])


def test_active_controller_versions_match_flux_v261_release():
    _, controllers = INVENTORY.load_capabilities()
    assert controllers["source-controller"]["upstreamVersion"] == "v1.6.0"
    assert controllers["kustomize-controller"]["upstreamVersion"] == "v1.6.0"
    assert controllers["helm-controller"]["upstreamVersion"] == "v1.3.0"
    assert controllers["notification-controller"]["upstreamVersion"] == "v1.6.0"


def test_repository_flux_inventory_is_parseable_and_has_core_resources():
    report = inventory("clusters/re8ch", "platform")
    assert report["parseErrors"] == []
    assert report["resourceCount"] > 20
    assert report["uniqueResourceCount"] <= report["resourceCount"]
    assert report["countsByController"]["source-controller"] > 0
    assert report["countsByController"]["kustomize-controller"] > 0
    assert report["countsByController"]["helm-controller"] > 0
    assert "image-automation-controller" in report["controllersWithoutDeclaredResources"]
    assert "image-reflector-controller" in report["controllersWithoutDeclaredResources"]


def test_root_flux_dependencies_and_secret_references_are_visible():
    report = inventory("clusters/re8ch")
    root = next(
        item for item in report["resources"]
        if item["kind"] == "Kustomization"
        and item["namespace"] == "flux-system"
        and item["name"] == "service-backend"
    )
    assert root["path"] == "./clusters/re8ch"
    assert root["prune"] is False
    assert root["references"]["sourceRef"] == {"kind": "GitRepository", "name": "service-backend"}
    assert {item["name"] for item in root["secretReferences"]} == {"sops-age"}


def test_executor_catalog_has_no_arbitrary_command_interface():
    catalog = yaml.safe_load(
        (ROOT / "operations/flux-lightweight/executor-operations.yaml").read_text()
    )["spec"]
    assert "arbitrary-shell" in catalog["forbiddenInterfaces"]
    assert "raw-kubectl-arguments" in catalog["forbiddenInterfaces"]
    assert catalog["accessProfiles"]["observer"]["clusterWrite"] is False
    for operation in catalog["operations"].values():
        assert "command" not in operation
        assert operation["profile"] in catalog["accessProfiles"]
