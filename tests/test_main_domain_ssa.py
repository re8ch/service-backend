import importlib.util
import subprocess
import sys
from pathlib import Path

import yaml

# LibYAML keeps SafeLoader constructors; pure Python remains a supported fallback.
YAML_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


ROOT = Path(__file__).resolve().parents[1]
DOMAINS = {"data-storage", "commerce-billing", "delivery-products", "operations-ui", "tenant-runtime"}
AUTONOMOUS = {"networking", "identity-access", "agent-runtime", "observability-metering"}


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str((ROOT / relative).parent))
    spec.loader.exec_module(module)
    return module


compiler = load_module("main_domain_compiler", Path("operations/releases/compile_main_domain_ssa.py"))
bundle = load_module("main_domain_bundle", Path("operations/releases/unified_bundle.py"))


def documents(path):
    return [item for item in yaml.load_all(path.read_text(), Loader=YAML_SAFE_LOADER) if item]


def test_generated_partition_is_current_complete_and_excludes_autonomous_domains():
    subprocess.run([sys.executable, "operations/releases/compile_main_domain_ssa.py", "--check"], cwd=ROOT, check=True)
    cohort = yaml.load((ROOT / "operations/releases/main-domain-ssa/cohort.yaml").read_text(), Loader=YAML_SAFE_LOADER)
    assert set(cohort["sourceDomains"]) == DOMAINS
    assert set(cohort["autonomousDomainsExcluded"]) == AUTONOMOUS
    assert cohort["releaseCount"] == 14
    source = {compiler.identity(item) for domain in DOMAINS
              for item in documents(ROOT / f"clusters/re8ch/domains/{domain}/resources.yaml")}
    ssa = {compiler.identity(item) for path in (ROOT / "operations/releases/main-domain-ssa").glob("main-*/resources.yaml")
           for item in documents(path)}
    legacy = {compiler.identity(item)
              for path in (ROOT / "clusters/re8ch/domains/main-legacy").glob("*/resources.yaml")
              for item in documents(path)}
    assert not ssa & legacy
    assert ssa | legacy == source
    assert set(cohort["deferredSSAObjects"]) == compiler.DEFERRED_SSA
    assert compiler.DEFERRED_SSA <= legacy
    assert compiler.DEFERRED_SSA.isdisjoint(ssa)


def test_every_ssa_release_is_signed_bundle_eligible_and_exactly_scoped():
    for contract_path in sorted((ROOT / "contracts/releases/main-domain").glob("*.yaml")):
        contract = yaml.load(contract_path.read_text(), Loader=YAML_SAFE_LOADER)
        package = ROOT / contract["spec"]["source"]["path"] / "resources.yaml"
        items = documents(package)
        namespace = contract["spec"]["target"]["namespace"]
        assert items and {item["metadata"]["namespace"] for item in items} == {namespace}
        assert all(item["kind"] not in compiler.FLUX_KINDS | compiler.CLUSTER_KINDS | {"Secret"} for item in items)
        assert all(not compiler.has_mutable_image(item) for item in items)
        bundle.compile_bundle(contract, package.read_bytes())


def test_main_domain_rbac_is_exact_name_update_only_with_bounded_rbac_takeover():
    docs = documents(ROOT / "clusters/re8ch/components/ops/github-actions-main-domain-executor-rbac.yaml")
    roles = [item for item in docs if item["kind"] == "Role"]
    bindings = [item for item in docs if item["kind"] == "RoleBinding"]
    states = [item for item in docs if item["kind"] == "ConfigMap"]
    cohort = yaml.load((ROOT / "operations/releases/main-domain-ssa/cohort.yaml").read_text(), Loader=YAML_SAFE_LOADER)
    assert len(roles) == len(bindings) == len(states) == cohort["releaseCount"]
    for role in roles:
        assert all(rule.get("resourceNames") for rule in role["rules"])
        for rule in role["rules"]:
            expected = {"get", "patch", "update", "delete"}
            if rule["apiGroups"] == ["rbac.authorization.k8s.io"] and rule["resources"] == ["roles"]:
                expected |= {"escalate", "bind"}
            assert set(rule["verbs"]) == expected
        assert all("*" not in rule.get("resources", []) + rule.get("resourceNames", []) for rule in role["rules"])
    assert {subject["name"] for binding in bindings for subject in binding["subjects"]} == {
        f"re8ch:release-executor:{domain}" for domain in DOMAINS
    }
    assert {role["metadata"]["labels"]["delivery.re8ch.com/rbac-profile"] for role in roles} == DOMAINS


def test_temporary_legacy_domain_is_safe_by_default_and_workflow_is_bound():
    reconcilers = documents(ROOT / "clusters/re8ch/flux-system/domain-reconcilers.yaml")
    names = {item["metadata"]["name"] for item in reconcilers}
    assert {f"reconcile-{domain}" for domain in DOMAINS}.isdisjoint(names)
    legacy = [item for item in reconcilers if item["metadata"]["name"].startswith("reconcile-main-legacy-")]
    assert {item["metadata"]["name"].removeprefix("reconcile-main-legacy-") for item in legacy} == DOMAINS
    state = {item["metadata"]["name"].removeprefix("reconcile-main-legacy-"): item["spec"]["suspend"]
             for item in legacy}
    assert state == {domain: False for domain in DOMAINS}
    assert all(item["spec"]["prune"] is False for item in legacy)
    assert {item["spec"]["path"] for item in legacy} == {
        f"./clusters/re8ch/domains/main-legacy/{domain}" for domain in DOMAINS
    }
    workflow = (ROOT / ".github/workflows/main-domain-release.yml").read_text()
    assert "contracts/releases/main-domain/*.yaml" in workflow
    for domain in DOMAINS:
        assert f"release-executor-{domain}" in workflow or "needs.validate.outputs.environment" in workflow
    assert "needs.validate.outputs.environment" in workflow
    assert "needs.validate.outputs.audience" in workflow
    assert "state-name --release-id" in workflow
    assert "cosign verify" in workflow


def test_each_main_domain_contract_uses_its_own_protected_execution_profile():
    authority = yaml.load((ROOT / "operations/releases/static-domain-authority.yaml").read_text(), Loader=YAML_SAFE_LOADER)
    profiles = authority["spec"]["domains"]
    cohort = yaml.load((ROOT / "operations/releases/main-domain-ssa/cohort.yaml").read_text(), Loader=YAML_SAFE_LOADER)
    declared = {item["domain"]: item for item in cohort["executionProfiles"]}
    assert set(declared) == DOMAINS
    for contract_path in sorted((ROOT / "contracts/releases/main-domain").glob("*.yaml")):
        contract = yaml.load(contract_path.read_text(), Loader=YAML_SAFE_LOADER)
        domain = contract["spec"]["authorityDomain"]
        execution = profiles[domain]["execution"]
        assert contract["spec"]["provenance"]["environment"] == execution["protectedEnvironment"]
        assert contract["spec"]["provenance"]["audience"] == execution["audience"]


def test_oidc_audience_reaches_login_and_ssa_token_refresh():
    workflow = yaml.load((ROOT / ".github/workflows/main-domain-release.yml").read_text(), Loader=YAML_SAFE_LOADER)
    job = workflow["jobs"]["execute"]
    steps = [step for step in job["steps"]
             if "ACTIONS_ID_TOKEN_REQUEST_URL" in step.get("run", "")
             or "ssa_executor.py" in step.get("run", "")]
    assert len(steps) == 2
    for step in steps:
        effective_env = {**workflow.get("env", {}), **job.get("env", {}), **step.get("env", {})}
        assert effective_env["EXECUTOR_AUDIENCE"] == "${{ needs.validate.outputs.audience }}"


def test_cloud_replay_skips_build_and_keeps_release_identity_and_serialization():
    workflow = yaml.load((ROOT / ".github/workflows/main-domain-release.yml").read_text(), Loader=YAML_SAFE_LOADER)
    jobs = workflow["jobs"]
    assert all(job["runs-on"] == "ubuntu-24.04" for job in jobs.values())
    assert "inputs.mode != 'reconcile'" in jobs["compile-sign"]["if"]
    assert "needs.compile-sign.result == 'skipped'" in jobs["execute"]["if"]
    assert "needs.validate.result == 'success'" in jobs["execute"]["if"]
    assert "inputs.execute" in jobs["execute"]["if"]
    assert workflow["concurrency"] == {"group": "main-domain-release-${{ inputs.contract }}", "cancel-in-progress": False}
    commands = "\n".join(step.get("run", "") for step in jobs["execute"]["steps"])
    assert "accepted-bundle --configmap" in commands
    assert "kubectl kustomize" not in commands and "flux push" not in commands
    assert "--force-conflicts" not in commands
    assert "cosign verify" in commands and "ssa_executor.py" in commands
    assert "|| executor_status=$?" in commands
    assert commands.index("operation_materialize.py state-patch") < commands.index('exit "$executor_status"')
    assert '.outcome == "healthy" and .health.status == "healthy"' in commands


def test_supabase_rest_has_one_native_writer_and_no_ssa_authority():
    identity = "apps/Deployment/supabase-pg-re8ch/supabase-rest"
    legacy = documents(ROOT / "clusters/re8ch/domains/main-legacy/data-storage/resources.yaml")
    assert identity in {compiler.identity(item) for item in legacy}
    assert not (ROOT / "contracts/releases/main-domain/main-data-storage-supabase-pg-re8ch.yaml").exists()
    assert not (ROOT / "operations/releases/main-domain-ssa/main-data-storage-supabase-pg-re8ch").exists()
    rbac = documents(ROOT / "clusters/re8ch/components/ops/github-actions-main-domain-executor-rbac.yaml")
    assert all(item["metadata"]["name"] != "main-data-storage-supabase-pg-re8ch" for item in rbac)
    owner = next(item for item in documents(ROOT / "clusters/re8ch/flux-system/domain-reconcilers.yaml")
                 if item["metadata"]["name"] == "reconcile-main-legacy-data-storage")
    assert owner["spec"]["suspend"] is False
    assert owner["spec"]["healthChecks"] == [dict(apiVersion="apps/v1", kind="Deployment",
                                               name="supabase-rest", namespace="supabase-pg-re8ch")]


def test_headlamp_has_native_health_reporting_and_no_ssa_writer():
    legacy = documents(ROOT / "clusters/re8ch/domains/main-legacy/operations-ui/resources.yaml")
    names = {item["metadata"]["name"] for item in legacy
             if item["kind"] == "Deployment" and item["metadata"].get("namespace") == "headlamp"}
    expected = {"headlamp", "headlamp-gateway-proxy", "headlamp-active-elector"}
    assert expected <= names
    assert not (ROOT / "contracts/releases/main-domain/main-operations-ui-headlamp.yaml").exists()
    assert not (ROOT / "operations/releases/main-domain-ssa/main-operations-ui-headlamp").exists()
    rbac = documents(ROOT / "clusters/re8ch/components/ops/github-actions-main-domain-executor-rbac.yaml")
    assert all(item["metadata"]["name"] not in {"main-operations-ui-headlamp", "main-operations-ui-kube-system"}
               for item in rbac)
    assert not (ROOT / "contracts/releases/main-domain/main-operations-ui-kube-system.yaml").exists()
    owner = next(item for item in documents(ROOT / "clusters/re8ch/flux-system/domain-reconcilers.yaml")
                 if item["metadata"]["name"] == "reconcile-main-legacy-operations-ui")
    assert owner["spec"]["suspend"] is False
    assert {item["name"] for item in owner["spec"]["healthChecks"]} == expected
    alerts = documents(ROOT / "clusters/re8ch/flux-system/github-control-plane.yaml")
    assert any(source.get("name") == owner["metadata"]["name"]
               for item in alerts if item["kind"] == "Alert" for source in item["spec"]["eventSources"])
