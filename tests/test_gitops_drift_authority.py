import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_audit():
    path = ROOT / "operations/gitops-drift/audit.py"
    spec = importlib.util.spec_from_file_location("gitops_drift_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_namespace_authority_separates_gitops_and_dynamic_tenants():
    audit = load_audit()
    declared = {"flux-system"}
    assert audit.authority("flux-system", {}, declared) == "gitops"
    assert audit.authority("customer-a", {"tenancy.re8ch.com/managed": "true", "tenancy.re8ch.com/managed-by": "tenant-control-controller"}, declared) == "tenant-controller"
    assert audit.authority("job-123", {"app.kubernetes.io/managed-by": "runtime"}, declared) == "runtime-controller"
    assert audit.authority("legacy", {}, declared) == "unclassified"


def test_all_static_namespace_declarations_have_gitops_authority():
    path = ROOT / "clusters/re8ch/components/toolhive/namespace-access-classification.yaml"
    docs = [item for item in yaml.safe_load_all(path.read_text()) if item]
    assert docs
    assert all(item["metadata"]["labels"]["re8ch.com/config-authority"] == "gitops" for item in docs)


def test_launchagents_execute_from_local_application_support():
    anchor = (ROOT / "operations/workspace-controller/macos/com.re8ch.service-backend-anchor-sync.plist").read_text()
    drift = (ROOT / "operations/gitops-drift/macos/com.re8ch.gitops-drift.plist").read_text()
    assert "/Library/Application Support/RE8CH/bin/sync-main-anchor.sh" in anchor
    assert "/Library/Application Support/RE8CH/bin/run-gitops-drift-audit.sh" in drift
    assert "/Volumes/A1/service-backend/operations/" not in anchor + drift
