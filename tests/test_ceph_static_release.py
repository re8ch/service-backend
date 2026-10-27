import json
from pathlib import Path

import pytest
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


def test_staticrelease_spec_updates_are_reserved_to_main_git_projection():
    crds = docs(ROOT / "clusters/re8ch/components/release-compat/api.yaml")
    static = next(item for item in crds if item["metadata"]["name"] == "staticreleases.delivery.re8ch.com")
    schema = static["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]
    assert "x-kubernetes-validations" not in schema
    resources = docs(ROOT / "clusters/re8ch/components/release-compat/admission.yaml")
    policy = next(item for item in resources if item["metadata"]["name"] == "delivery-staticrelease-git-authority")
    expression = policy["spec"]["validations"][0]["expression"]
    assert "object.spec == oldObject.spec" in expression
    assert "system:serviceaccount:flux-system:kustomize-controller" in expression


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


def test_flux_compat_admission_handles_optional_helmrelease_fields():
    resources = docs(ROOT / "clusters/re8ch/components/release-compat/admission.yaml")
    policy = next(item for item in resources if item["metadata"]["name"] == "flux-compat-projections")
    expression = policy["spec"]["validations"][1]["expression"]
    for field in ("timeout", "releaseName", "targetNamespace", "storageNamespace", "valuesFrom", "dependsOn", "install", "upgrade", "rollback"):
        assert f"!has(object.spec.{field}) && !has(oldObject.spec.{field})" in expression


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


def test_workflow_accepts_cloud_requests_and_splits_identities():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ceph-static-release.yml").read_text())
    inputs = workflow[True]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"operation_id", "release", "action", "approved_digest", "reason", "approval_ref", "force_objects"}
    assert inputs["operation_id"]["required"] is False
    assert workflow["concurrency"] == {"group": "ceph-runtime-operations", "cancel-in-progress": False, "queue": "max"}
    text = (ROOT / ".github/workflows/ceph-static-release.yml").read_text()
    assert "release-executor-ceph-infra" in text
    assert "release-executor-ceph-runtime" in text
    assert "for stage in crd-schema cluster-rbac" in text
    assert "stages=(runtime custom-resources)" in text
    assert "for stage in storage-class health-acceptance" in text
    assert "--no-stage-apply" in text
    assert "bundle has no infra stages" in text
    assert "bundle has no runtime stages" in text
    assert 'approved="$(jq -r .spec.approvedDigest' in text
    assert '--current-bundle "$RUNNER_TEMP/current-bundle/release.bundle" --rollback-stage runtime' in text
    assert 'if [[ "$ACTION" != rollback ]]' in text
    assert 'kubectl apply --server-side --field-manager="re8ch-release-state/${RELEASE}"' in text
    assert "kubectl patch configmap" not in text


def test_rollback_operations_require_an_explicit_approval_reference():
    crds = docs(ROOT / "clusters/re8ch/components/release-compat/api.yaml")
    operation = next(item for item in crds if item["metadata"]["name"] == "releaseoperations.delivery.re8ch.com")
    validations = operation["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"][
        "x-kubernetes-validations"]
    assert any("self.action != 'rollback' || has(self.approvalRef)" in item["rule"] for item in validations)


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
        assert pod["nodeSelector"] == {"kubernetes.io/hostname": "a1-wsl-zt"}
        assert {item["key"] for item in pod["tolerations"]} == {
            "node-role.kubernetes.io/control-plane",
            "scheduling.re8ch.com/capacity-window",
        }
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
    assert job["metadata"]["name"] == "release-artifact-mirror-123-111111111111"
    assert pod["automountServiceAccountToken"] is False
    assert pod["hostNetwork"] is True
    assert pod["dnsPolicy"] == "ClusterFirstWithHostNet"
    assert pod["nodeSelector"] == {"kubernetes.io/hostname": "r640"}
    assert pod["securityContext"] == {"fsGroup": 65532, "fsGroupChangePolicy": "OnRootMismatch"}
    assert {item["name"] for item in pod["initContainers"]} == {"copy-artifact", "copy-signature"}
    assert all(item["env"] == [{"name": "DOCKER_CONFIG", "value": "/auth"}] for item in pod["initContainers"])
    assert all("--registry-config" not in item["args"] for item in pod["initContainers"])
    assert {item["image"] for item in pod["initContainers"]} == {
        "registry.re8ch.com/proxy-ghcr/oras-project/oras@sha256:a3ce6b38d4c510ea9fdc0449b942ea44fb790f157e79b5e7e30b1e7460fe5579"
    }
    assert pod["containers"][0]["image"] == (
        "registry.re8ch.com/functions-shared/cosign@sha256:57c0e93a829ae213ab4273b5bd31bc24812043183040882d7cc215a12b5a6870"
    )
    for container in [*pod["initContainers"], *pod["containers"]]:
        assert container["securityContext"]["runAsUser"] == 65532
        assert container["securityContext"]["runAsGroup"] == 65532
    assert pod["volumes"][0]["secret"]["defaultMode"] == 0o440
    assert pod["containers"][0]["name"] == "verify-signature"
    assert pod["volumes"][0]["secret"]["secretName"] == "release-artifact-mirror-auth"


def test_protected_harbor_mirror_jobs_are_operation_scoped():
    import importlib.util

    path = ROOT / "operations/releases/protected_harbor_mirror.py"
    spec = importlib.util.spec_from_file_location("protected_harbor_mirror_scoped", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    signer = "https://github.com/re8ch/service-backend/.github/workflows/ceph-shadow-compile.yml@refs/heads/main"
    first = module.manifest(
        "ghcr.io/re8ch/service-backend-chart/rook-ceph",
        "registry.re8ch.com/functions-shared/rook-ceph",
        "sha256:" + "1" * 64,
        "run-123",
        signer,
        "123",
    )
    second = module.manifest(
        "ghcr.io/re8ch/service-backend-chart/ceph-csi-drivers",
        "registry.re8ch.com/functions-shared/ceph-csi-drivers",
        "sha256:" + "2" * 64,
        "run-456",
        signer,
        "456",
    )
    assert first["metadata"]["name"] != second["metadata"]["name"]
    assert first["metadata"]["annotations"]["delivery.re8ch.com/operation-id"] == "123"
    assert second["metadata"]["annotations"]["delivery.re8ch.com/operation-id"] == "456"


def test_mirror_wait_refreshes_only_the_exact_actions_oidc_audience(monkeypatch):
    import importlib.util
    import io
    import urllib.parse

    path = ROOT / "operations/releases/protected_harbor_mirror.py"
    spec = importlib.util.spec_from_file_location("protected_harbor_mirror_token", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv(
        "ACTIONS_ID_TOKEN_REQUEST_URL",
        "https://pipelines.actions.githubusercontent.com/example/token?api-version=2.0",
    )
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "request-token")
    captured = {}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    def open_token(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.headers["Authorization"]
        assert timeout == 30
        return Response(b'{"value":"short-lived-token"}')

    monkeypatch.setattr(module.urllib.request, "urlopen", open_token)
    assert module.fresh_actions_token() == "short-lived-token"
    assert captured["authorization"] == "bearer request-token"
    query = urllib.parse.parse_qs(urllib.parse.urlparse(captured["url"]).query)
    assert query["audience"] == [module.PUBLISHER_AUDIENCE]

    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://example.com/token")
    with pytest.raises(RuntimeError, match="unexpected Actions OIDC endpoint"):
        module.fresh_actions_token()


def cloud_request_module():
    import importlib.util
    import sys
    sys.path.insert(0, str(ROOT / "operations/releases"))
    spec = importlib.util.spec_from_file_location("ceph_cloud_request", ROOT / "operations/releases/ceph_cloud_request.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_direct_ceph_request_is_deterministic_git_bound_and_needs_no_local_cr():
    module = cloud_request_module()
    inputs = {"release": "rook-ceph", "action": "reconcile"}
    one, release = module.prepare(inputs, run_id="12345", actor="test-actor")
    two, _ = module.prepare(inputs, run_id="12345", actor="test-actor")
    assert one == two
    assert one["spec"]["approvedDigest"] == release["spec"]["bundleDigest"]
    assert one["spec"]["contractDigest"] == release["spec"]["contractDigest"]
    assert "bundleRepository" not in one["spec"]
    other, _ = module.prepare(inputs, run_id="12346", actor="test-actor")
    assert other["metadata"]["uid"] != one["metadata"]["uid"]


@pytest.mark.parametrize("change", [
    {"release": "other"}, {"action": "delete"}, {"approved_digest": "sha256:" + "f" * 64},
    {"action": "rollback"}, {"force_objects": '["core/Secret/rook-ceph/token"]'},
    {"action": "force-reconcile", "force_objects": '["core/Secret/rook-ceph/token"]'},
    {"reason": "a" * 513}, {"force_objects": '{}'}])
def test_direct_ceph_request_rejects_unreviewed_inputs(change):
    module = cloud_request_module()
    with pytest.raises(ValueError):
        module.prepare({"release": "rook-ceph", **change}, run_id="12345", actor="test")


def test_legacy_ceph_operation_is_expiry_checked_and_cannot_mix_direct_inputs():
    import datetime as dt
    module = cloud_request_module()
    legacy, _ = module.prepare({"release": "rook-ceph"}, run_id="12345", actor="test")
    legacy["metadata"]["creationTimestamp"] = dt.datetime.now(dt.timezone.utc).isoformat()
    assert module.prepare({}, run_id="12346", actor="test", legacy=legacy)[0] == legacy
    with pytest.raises(ValueError, match="combined"):
        module.prepare({"release": "rook-ceph"}, run_id="12346", actor="test", legacy=legacy)
    legacy["metadata"]["creationTimestamp"] = "2020-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="expired"):
        module.prepare({}, run_id="12346", actor="test", legacy=legacy)


def test_ceph_jobs_share_cloud_snapshot_and_preserve_failed_evidence():
    raw = (ROOT / ".github/workflows/ceph-static-release.yml").read_text()
    workflow = yaml.safe_load(raw)
    assert raw.count("name: 'ceph-request-${{ github.run_id }}'") == 3
    assert "kubectl get releaseoperation" not in raw
    assert "kubectl get staticrelease" not in raw
    assert "set-credentials" not in raw and "Authorization: bearer" not in raw
    for job in workflow["jobs"].values():
        evidence = [step for step in job["steps"] if step.get("name") == "Preserve workflow failure evidence"]
        assert len(evidence) == 1 and evidence[0]["if"] == "failure()"
        uploads = [step for step in job["steps"] if "upload-artifact" in step.get("uses", "") and "result.json" in step.get("with", {}).get("path", "")]
        assert len(uploads) == 1 and uploads[0]["if"] == "always()"
        assert "workflow-failure.json" in uploads[0]["with"]["path"]
