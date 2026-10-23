import importlib.util
import subprocess
import sys
from pathlib import Path

import yaml


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
    return [item for item in yaml.safe_load_all(path.read_text()) if item]


def test_generated_partition_is_current_complete_and_excludes_autonomous_domains():
    subprocess.run([sys.executable, "operations/releases/compile_main_domain_ssa.py", "--check"], cwd=ROOT, check=True)
    cohort = yaml.safe_load((ROOT / "operations/releases/main-domain-ssa/cohort.yaml").read_text())
    assert set(cohort["sourceDomains"]) == DOMAINS
    assert set(cohort["autonomousDomainsExcluded"]) == AUTONOMOUS
    assert cohort["releaseCount"] == 19
    source = {compiler.identity(item) for domain in DOMAINS
              for item in documents(ROOT / f"clusters/re8ch/domains/{domain}/resources.yaml")}
    ssa = {compiler.identity(item) for path in (ROOT / "operations/releases/main-domain-ssa").glob("main-*/resources.yaml")
           for item in documents(path)}
    legacy = {compiler.identity(item)
              for path in (ROOT / "clusters/re8ch/domains/main-legacy").glob("*/resources.yaml")
              for item in documents(path)}
    assert not ssa & legacy
    assert ssa | legacy == source


def test_every_ssa_release_is_signed_bundle_eligible_and_exactly_scoped():
    for contract_path in sorted((ROOT / "contracts/releases/main-domain").glob("*.yaml")):
        contract = yaml.safe_load(contract_path.read_text())
        package = ROOT / contract["spec"]["source"]["path"] / "resources.yaml"
        items = documents(package)
        namespace = contract["spec"]["target"]["namespace"]
        assert items and {item["metadata"]["namespace"] for item in items} == {namespace}
        assert all(item["kind"] not in compiler.FLUX_KINDS | compiler.CLUSTER_KINDS | {"Secret"} for item in items)
        assert all(not compiler.has_mutable_image(item) for item in items)
        bundle.compile_bundle(contract, package.read_bytes())


def test_main_domain_rbac_is_exact_name_update_only():
    docs = documents(ROOT / "clusters/re8ch/components/ops/github-actions-main-domain-executor-rbac.yaml")
    roles = [item for item in docs if item["kind"] == "Role"]
    bindings = [item for item in docs if item["kind"] == "RoleBinding"]
    states = [item for item in docs if item["kind"] == "ConfigMap"]
    assert len(roles) == len(bindings) == len(states) == 19
    for role in roles:
        assert all(rule.get("resourceNames") for rule in role["rules"])
        assert all(set(rule["verbs"]) == {"get", "patch", "update", "delete"} for rule in role["rules"])
        assert all("*" not in rule.get("resources", []) + rule.get("resourceNames", []) for rule in role["rules"])
    assert {subject["name"] for binding in bindings for subject in binding["subjects"]} == {
        "re8ch:release-executor:main-domain"
    }


def test_temporary_legacy_domain_is_safe_by_default_and_workflow_is_bound():
    reconcilers = documents(ROOT / "clusters/re8ch/flux-system/domain-reconcilers.yaml")
    legacy = [item for item in reconcilers if item["metadata"]["name"].startswith("reconcile-main-legacy-")]
    assert {item["metadata"]["name"].removeprefix("reconcile-main-legacy-") for item in legacy} == DOMAINS
    state = {item["metadata"]["name"].removeprefix("reconcile-main-legacy-"): item["spec"]["suspend"]
             for item in legacy}
    assert state == {domain: domain != "operations-ui" for domain in DOMAINS}
    assert all(item["spec"]["prune"] is False for item in legacy)
    assert {item["spec"]["path"] for item in legacy} == {
        f"./clusters/re8ch/domains/main-legacy/{domain}" for domain in DOMAINS
    }
    workflow = (ROOT / ".github/workflows/main-domain-release.yml").read_text()
    assert "contracts/releases/main-domain/*.yaml" in workflow
    assert "https://kube-api.re8ch.com/release-executor-main-domain" in workflow
    assert "environment: release-main-domain" in workflow
    assert "state-name --release-id" in workflow
    assert "cosign verify" in workflow
