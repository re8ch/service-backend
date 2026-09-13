import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("tenant_select", ROOT / "operations/tenant-conformance/select.py")
SELECTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SELECTOR)


def manifest():
    return yaml.safe_load((ROOT / "operations/tenant-conformance/modules.yaml").read_text())


def test_private_change_selects_only_module_and_dependency():
    assert SELECTOR.select(manifest(), ["plugins/tenant-observability.yaml"]) == ["tenant-core", "tenant-observability"]


def test_core_change_selects_reverse_dependency_closure():
    selected = SELECTOR.select(manifest(), ["clusters/re8ch/charts/birfucate-metering/crds/tenancy.re8ch.com.yaml"])
    assert selected == ["tenant-byoc", "tenant-core", "tenant-database", "tenant-kubernetes", "tenant-observability", "tenant-registry"]


def test_non_tenant_change_selects_nothing():
    assert SELECTOR.select(manifest(), ["README.md"]) == []

