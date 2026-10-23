import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]


def docs(path):
    return [item for item in yaml.safe_load_all(path.read_text()) if isinstance(item, dict)]


def test_three_locked_ceph_contracts_and_dependencies_are_exact():
    lock = yaml.safe_load((ROOT / "contracts/releases/ceph/chart-locks.yaml").read_text())
    assert [item["name"] for item in lock["charts"]] == ["rook-ceph", "ceph-csi-drivers", "rook-ceph-cluster"]
    assert [item["objectCount"] for item in lock["charts"]] == [83, 23, 7]
    assert lock["charts"][1]["dependsOn"] == ["rook-ceph"]
    assert lock["charts"][2]["dependsOn"] == ["rook-ceph", "ceph-csi-drivers"]
    assert all(item["sha256"].startswith("sha256:") and len(item["sha256"]) == 71 for item in lock["charts"])
    assert all(item["mirror"].startswith("oci://registry.re8ch.com/functions-shared/") for item in lock["charts"])
    assert all(item["sourceMirror"].startswith("oci://ghcr.io/re8ch/service-backend-chart/") for item in lock["charts"])
    schema = json.loads((ROOT / "schemas/release-contract.schema.json").read_text())
    for name in ("rook-ceph", "ceph-csi-drivers", "rook-ceph-cluster"):
        contract = yaml.safe_load((ROOT / f"contracts/releases/ceph/{name}.yaml").read_text())
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(contract)
        assert [stage["name"] for stage in contract["spec"]["lifecycle"]["stages"]][-1] == "health-acceptance"
        assert set(contract["spec"]["lifecycle"]["manualRollbackKinds"]) >= {"CustomResourceDefinition", "StorageClass", "CephCluster"}


def test_compatibility_surface_is_projection_only_and_controller_has_no_workload_authority():
    releases = docs(ROOT / "clusters/re8ch/components/rook-ceph/releases.yaml")
    projections = [item for item in releases if item["kind"] in {"HelmRelease", "HelmRepository"}]
    assert len(projections) == 5
    assert all(item["metadata"]["labels"]["delivery.re8ch.com/compat-projection"] == "true" for item in projections)
    assert all(item["spec"]["suspend"] is True for item in projections if item["kind"] == "HelmRelease")
    rbac = docs(ROOT / "clusters/re8ch/components/release-compat/rbac.yaml")
    controller = next(item for item in rbac if item["kind"] == "ClusterRole" and item["metadata"]["name"] == "release-compat-controller")
    resources = {resource for rule in controller["rules"] for resource in rule["resources"]}
    assert not resources & {"secrets", "pods", "pods/exec", "serviceaccounts/token", "deployments", "daemonsets"}


def test_flux_compat_admission_ignores_unlabelled_flux_consumers():
    resources = docs(ROOT / "clusters/re8ch/components/release-compat/admission.yaml")
    policies = {
        item["metadata"]["name"]: item
        for item in resources
        if item["kind"] == "ValidatingAdmissionPolicy"
    }
    for name in ("flux-compat-projections", "flux-source-compat-projections"):
        expression = policies[name]["spec"]["matchConditions"][0]["expression"]
        assert "'delivery.re8ch.com/compat-projection' in object.metadata.labels" in expression


def test_ceph_executor_identities_deny_forbidden_capabilities_by_construction():
    resources = docs(ROOT / "clusters/re8ch/components/release-compat/ceph-executor-rbac.yaml")
    roles = {item["metadata"]["name"]: item for item in resources if item["kind"] == "ClusterRole"}
    infra = roles["release-executor-ceph-infra"]
    runtime = roles["release-executor-ceph-runtime"]
    forbidden = {"secrets", "serviceaccounts/token", "pods/exec"}
    assert not {resource for rule in infra["rules"] for resource in rule["resources"]} & forbidden
    cluster_resources = {"customresourcedefinitions", "clusterroles", "clusterrolebindings", "storageclasses"}
    runtime_writes = {resource for rule in runtime["rules"] if set(rule["verbs"]) & {"create", "patch", "update", "delete"} for resource in rule["resources"]}
    assert not runtime_writes & cluster_resources


def test_workflow_accepts_only_operation_id_and_splits_identities():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ceph-static-release.yml").read_text())
    inputs = workflow[True]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"operation_id"}
    text = (ROOT / ".github/workflows/ceph-static-release.yml").read_text()
    assert "release-executor-ceph-infra" in text
    assert "release-executor-ceph-runtime" in text
    assert "--stage crd-schema --stage cluster-rbac --partial" in text
    assert "--stage runtime --stage custom-resources --partial" in text


def test_metrics_golden_and_compatibility_additions_are_declared():
    fixture = yaml.safe_load((ROOT / "operations/releases/metrics-golden/helm-controller-v2.6.1.yaml").read_text())
    assert {"controller_runtime_reconcile_total", "workqueue_depth", "gotk_reconcile_duration_seconds"} <= set(fixture["families"])
    assert set(fixture["compatibilityAdditions"]) == {"gotk_reconcile_condition", "gotk_resource_info", "gotk_suspend_status"}


def test_compat_controller_uses_authenticated_internal_registry():
    resources = docs(ROOT / "clusters/re8ch/components/release-compat/controller.yaml")
    deployments = [item for item in resources if item["kind"] == "Deployment"]
    assert len(deployments) == 2
    for deployment in deployments:
        pod = deployment["spec"]["template"]["spec"]
        assert pod["imagePullSecrets"] == [{"name": "gitops-oci-reader"}]
        assert pod["containers"][0]["image"].startswith(
            "registry.re8ch.com/functions-shared/release-compat-controller@sha256:"
        )


def test_release_workflows_use_oidc_protected_in_cluster_harbor_mirror():
    for path in (
        ROOT / ".github/workflows/release-compat-controller.yml",
        ROOT / ".github/workflows/ceph-shadow-compile.yml",
    ):
        workflow = path.read_text()
        assert "release-artifact-publisher" in workflow
        assert "protected_harbor_mirror.py --apply" in workflow
        assert "registry.re8ch.com/functions-shared" in workflow
        assert "HARBOR_RELEASE_PUBLISHER_PASSWORD" not in workflow


def test_protected_harbor_mirror_job_is_fixed_and_has_no_api_token():
    import importlib.util

    path = ROOT / "operations/releases/protected_harbor_mirror.py"
    spec = importlib.util.spec_from_file_location("protected_harbor_mirror", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    digest = "sha256:" + "1" * 64
    job = module.manifest(
        "ghcr.io/re8ch/service-backend-chart/rook-ceph",
        "registry.re8ch.com/functions-shared/rook-ceph",
        digest,
        "run-123",
        "https://github.com/re8ch/service-backend/.github/workflows/ceph-shadow-compile.yml@refs/heads/main",
        "123",
    )
    pod = job["spec"]["template"]["spec"]
    assert job["metadata"]["name"] == "release-artifact-mirror"
    assert pod["automountServiceAccountToken"] is False
    assert {item["name"] for item in pod["initContainers"]} == {"copy-artifact", "copy-signature"}
    assert pod["containers"][0]["name"] == "verify-signature"
    assert pod["volumes"][0]["secret"]["secretName"] == "release-artifact-mirror-auth"
