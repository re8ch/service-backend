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
    assert cohort["releaseCount"] == 21
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
        assert items
        if contract["spec"]["authorization"]["allowClusterScoped"]:
            assert all(not item["metadata"].get("namespace") and item["kind"] in compiler.CLUSTER_EXECUTABLE for item in items)
            assert contract["spec"]["lifecycle"]["prune"] is False
            assert contract["spec"]["lifecycle"]["deletionAllowlist"] == []
        else:
            assert {item["metadata"]["namespace"] for item in items} == {namespace}
            assert all(item["kind"] not in compiler.CLUSTER_KINDS for item in items)
        assert all(item["kind"] not in compiler.FLUX_KINDS | compiler.RBAC_KINDS | {"Secret"} for item in items)
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
            expected = {"get", "patch", "update"}
            role_contract = yaml.load((ROOT / "contracts/releases/main-domain" / (role["metadata"]["name"] + ".yaml")).read_text(), Loader=YAML_SAFE_LOADER)
            if role_contract["spec"]["lifecycle"]["prune"]:
                expected.add("delete")
            retired = yaml.load((ROOT / "operations/releases/static-domain-authority.yaml").read_text(), Loader=YAML_SAFE_LOADER)["spec"].get("retiredReleases", {})
            if role["metadata"]["name"] in retired:
                expected = {"get"}
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


def test_former_native_namespaced_objects_have_bounded_cloud_contracts():
    expected = {
        "main-data-storage-supabase-pg-re8ch": {"apps/Deployment/supabase-pg-re8ch/supabase-rest"},
        "main-operations-ui-headlamp": {"apps/Deployment/headlamp/headlamp", "apps/Deployment/headlamp/headlamp-gateway-proxy", "apps/Deployment/headlamp/headlamp-active-elector"},
        "main-operations-ui-kube-system": {"core/ConfigMap/kube-system/public-edge-catalog", "core/ConfigMap/kube-system/public-site-governance"},
    }
    legacy = {compiler.identity(item) for path in (ROOT / "clusters/re8ch/domains/main-legacy").glob("*/resources.yaml") for item in documents(path)}
    for release, identities in expected.items():
        path = ROOT / "contracts/releases/main-domain" / f"{release}.yaml"
        contract = yaml.load(path.read_text(), Loader=YAML_SAFE_LOADER)
        items = documents(ROOT / contract["spec"]["source"]["path"] / "resources.yaml")
        assert identities <= {compiler.identity(item) for item in items}
        assert not identities & legacy
        assert contract["spec"]["authorization"]["allowClusterScoped"] is False


def test_legacy_report_exposes_every_remaining_capability_gap():
    cohort = yaml.load((ROOT / "operations/releases/main-domain-ssa/cohort.yaml").read_text(), Loader=YAML_SAFE_LOADER)
    legacy = {compiler.identity(item): item for path in (ROOT / "clusters/re8ch/domains/main-legacy").glob("*/resources.yaml") for item in documents(path)}
    report = {item["identity"]: item["reason"] for item in cohort["remainingObjects"]}
    assert set(report) == set(legacy)
    assert len(report) == cohort["legacyObjectCount"]
    assert sum(cohort["remainingCapabilities"].values()) == len(legacy)
    domains = {item["identity"]: item["domain"] for item in cohort["remainingObjects"]}
    assert {identity: compiler.legacy_reason(item, domains[identity]) for identity, item in legacy.items()} == report
    assert all(report[identity] == "approved-secret-injection-required" for identity, item in legacy.items()
               if item["kind"] == "Secret" and item["metadata"].get("labels", {}).get("delivery.re8ch.com/retention") != "retained-data")


def test_old_native_hint_does_not_block_eligible_namespaced_workload():
    item = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "test", "namespace": "ops", "annotations": {"delivery.re8ch.com/reconciler": "native-flux"}}}
    assert compiler.legacy_reason(item) is None
    item["metadata"]["labels"] = {"delivery.re8ch.com/retention": "retained-data"}
    assert compiler.legacy_reason(item) == "retained-data-no-automatic-mutation"


def test_cluster_bootstrap_has_no_rbac_mutation_or_deletion_authority():
    rbac = documents(ROOT / 'clusters/re8ch/components/ops/github-actions-main-domain-executor-rbac.yaml')
    roles = [item for item in rbac if item['kind'] == 'ClusterRole']
    assert len(roles) == 4
    for role in roles:
        assert role['metadata']['name'].endswith('-cluster-bootstrap')
        for rule in role['rules']:
            assert rule['resourceNames'] and '*' not in rule['resourceNames']
            assert set(rule['verbs']) == {'get', 'patch', 'update'}
            assert 'rbac.authorization.k8s.io' not in rule['apiGroups']
    for path in (ROOT / 'contracts/releases/main-domain').glob('*-cluster-bootstrap.yaml'):
        contract = yaml.load(path.read_text(), Loader=YAML_SAFE_LOADER)
        assert contract['spec']['target']['namespace'] == 'ops'
        assert contract['spec']['lifecycle']['prune'] is False
        assert not contract['spec']['lifecycle']['deletionAllowlist']
        assert set(contract['spec']['lifecycle']['protectedKinds']) >= compiler.CLUSTER_EXECUTABLE
        assert contract['spec']['authorization']['allowClusterScoped'] is True
        assert contract['spec']['authorization']['requireExisting'] is True


def test_headlamp_rbac_stays_platform_owned_and_cannot_be_pruned_on_handoff():
    contract = yaml.load((ROOT / 'contracts/releases/main-domain/main-operations-ui-headlamp.yaml').read_text(), Loader=YAML_SAFE_LOADER)
    assert contract['spec']['lifecycle']['prune'] is False
    assert contract['spec']['lifecycle']['deletionAllowlist'] == []
    items = documents(ROOT / contract['spec']['source']['path'] / 'resources.yaml')
    assert all(item['kind'] not in compiler.RBAC_KINDS for item in items)
    legacy = documents(ROOT / 'clusters/re8ch/domains/main-legacy/operations-ui/resources.yaml')
    assert {(item['kind'], item['metadata']['name']) for item in legacy if item['kind'] in compiler.RBAC_KINDS} >= {
        ('Role', 'headlamp-active-elector'), ('RoleBinding', 'headlamp-active-elector')}


def test_admission_api_defaults_preserve_explicit_constraints():
    item = {"apiVersion": "admissionregistration.k8s.io/v1", "kind": "ValidatingAdmissionPolicy",
            "spec": {"matchConstraints": {"resourceRules": [{"resources": ["persistentvolumeclaims"]}]}}}
    compiler.canonical_admission_defaults(item)
    constraints = item["spec"]["matchConstraints"]
    assert constraints == {"matchPolicy": "Equivalent", "namespaceSelector": {}, "objectSelector": {},
                           "resourceRules": [{"resources": ["persistentvolumeclaims"], "scope": "*"}]}
    constraints.update(matchPolicy="Exact", namespaceSelector={"matchLabels": {"team": "ops"}})
    constraints["resourceRules"][0]["scope"] = "Namespaced"
    compiler.canonical_admission_defaults(item)
    assert constraints["matchPolicy"] == "Exact"
    assert constraints["namespaceSelector"] == {"matchLabels": {"team": "ops"}}
    assert constraints["resourceRules"][0]["scope"] == "Namespaced"


def test_kyverno_crd_defaults_preserve_explicit_policy_values():
    item = {"apiVersion": "kyverno.io/v1", "kind": "ClusterPolicy", "spec": {"rules": [
        {"name": "defaulted", "validate": {"message": "unchanged"}},
        {"name": "explicit", "skipBackgroundRequests": False, "validate": {"allowExistingViolations": False}}]}}
    compiler.canonical_admission_defaults(item)
    assert item["spec"]["admission"] is True and item["spec"]["emitWarning"] is False
    first, second = item["spec"]["rules"]
    assert first["skipBackgroundRequests"] is True
    assert first["validate"] == {"message": "unchanged", "allowExistingViolations": True}
    assert second["skipBackgroundRequests"] is False
    assert second["validate"]["allowExistingViolations"] is False


def test_configmap_binary_data_matches_api_canonical_base64_without_changing_bytes():
    import base64
    source = {compiler.identity(item): item for item in documents(
        ROOT / "clusters/re8ch/domains/operations-ui/resources.yaml") if item.get("binaryData")}
    rendered = documents(ROOT / "operations/releases/main-domain-ssa/main-operations-ui-headlamp/resources.yaml")
    checked = 0
    for item in rendered:
        for key, value in item.get("binaryData", {}).items():
            original = source[compiler.identity(item)]["binaryData"][key]
            assert value == base64.b64encode(base64.b64decode(original)).decode()
            assert not any(char.isspace() for char in value)
            checked += 1
    assert checked == 6


def test_node_adoption_leaves_runtime_taints_with_existing_authority():
    source = next(item for item in documents(ROOT / "clusters/re8ch/domains/data-storage/resources.yaml")
                  if item["kind"] == "Node" and item["metadata"]["name"] == "b1-wsl-zt")
    assert source["spec"]["taints"] == [{"key": "storage.re8ch.com/coordinator", "value": "true", "effect": "NoSchedule"}]
    rendered = documents(ROOT / "operations/releases/main-domain-ssa/main-data-storage-cluster-bootstrap/resources.yaml")
    nodes = [item for item in rendered if item["kind"] == "Node"]
    assert nodes and all("spec" not in item for item in nodes)
    adopted = next(item for item in nodes if item["metadata"]["name"] == "b1-wsl-zt")
    assert adopted["metadata"] == source["metadata"]
    cohort = yaml.load((ROOT / "operations/releases/main-domain-ssa/cohort.yaml").read_text(), Loader=YAML_SAFE_LOADER)
    assert {"domain": "data-storage", "identity": "core/Node/_/b1-wsl-zt", "field": "spec.taints",
            "authority": "existing-runtime-node-authority", "reason": "cloud-adoption-manages-static-metadata-only"} in cohort["omissions"]


def test_reset_requires_certified_reuse_and_materializes_only_reset():
    import os
    import subprocess
    workflow = yaml.load((ROOT / '.github/workflows/main-domain-release.yml').read_text(), Loader=YAML_SAFE_LOADER)
    assert 'reset' in workflow.get('on', workflow.get(True))['workflow_dispatch']['inputs']['mode']['options']
    step = next(s for s in workflow['jobs']['validate']['steps'] if s.get('name') == 'Validate artifact reuse selection')
    for artifact, source, expected in [('', '', False), ('sha256:' + 'a' * 64, '', False), ('sha256:' + 'a' * 64, 'b' * 40, True)]:
        result = subprocess.run(['bash', '-e', '-c', step['run']], env={**os.environ, 'MODE': 'reset', 'REUSE_DIGEST': artifact, 'REUSE_SOURCE_COMMIT': source}, capture_output=True)
        assert (result.returncode == 0) is expected
    commands = '\n'.join(s.get('run', '') for s in workflow['jobs']['execute']['steps'])
    assert 'if [[ "$RELEASE_MODE" == reset ]]; then takeover_args=(--action reset); fi' in commands
    assert 'if [[ "$RELEASE_MODE" != publish ]]; then exit 1; fi' in commands
