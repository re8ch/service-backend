import ast
import importlib.util
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("impact", ROOT / "operations/ci/impact.py")
impact = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(impact)


def test_focused_source_runs_unit_without_unrelated_scans():
    plan = impact.select(["operations/releases/domain_publish.py"])
    assert plan["jobs"]["unit"]
    assert not any(value for key, value in plan["jobs"].items() if key != "unit")
    assert not any(plan["checks"].values())


def test_docs_skip_optional_jobs_and_full_enables_them():
    assert not any(impact.select(["README.md"])["jobs"].values())
    plan = impact.select([], full=True)
    assert all(plan["jobs"].values())
    assert all(plan["checks"].values())


def test_release_lock_keeps_schema_and_render_checks():
    plan = impact.select(["releases/platform-adapter.yaml"])
    assert plan["jobs"]["schema"] and plan["jobs"]["render"]
    assert plan["checks"]["schemas"]
    assert not plan["checks"]["fabric"]


def test_unknown_input_enables_every_check():
    plan = impact.select(["operations/new-validator.py"])
    assert all(plan["jobs"].values())
    assert all(plan["checks"].values())


def test_changed_domain_source_selects_bundle_verification():
    assert impact.select(["clusters/re8ch/domains/networking/source/kustomization.yaml"])["checks"]["domain_bundles"]


def test_every_planned_job_is_wired_and_aggregated():
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    for name in impact.select([], full=True)["jobs"]:
        assert jobs[name]["if"] == f"fromJSON(needs.impact.outputs.jobs)['{name}']"
        assert name in jobs["control-plane-validate"]["needs"]
    assert "if" not in jobs["secret-scan"]
    assert "if" not in jobs["policy"]


def test_generated_fabric_consumers_and_public_authority_keep_their_checks():
    spec = importlib.util.spec_from_file_location("fabric_model", ROOT / "operations/network/fabric/fabric_model.py")
    fabric = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fabric)
    consumers = [path for paths in fabric.OUTPUTS.values() for path in paths]
    for path in consumers + [fabric.HOST_CONTRACT]:
        assert impact.select([path])["checks"]["fabric"], path
    assert impact.select(["platform/among-clusters/release.yaml"])["checks"]["public_authority"]


def test_every_kustomize_root_has_one_owner():
    impact.load_catalog()


def test_network_change_is_incremental():
    plan = impact.select(["platform/advanced-fabric/dns/kustomization.yaml"])
    assert plan["domains"] == ["networking"]
    assert not plan["full"]
    assert "platform/observability/production" not in plan["roots"]
    assert plan["roots"] == ["platform/advanced-fabric"]


def test_shared_ci_change_fails_safe_to_full():
    for path in ("operations/ci/impact.py", "tests/conftest.py", "pyproject.toml"):
        plan = impact.select([path])
        assert plan["full"], path
        assert len(plan["domains"]) == len(impact.load_catalog()["domains"])


def test_domain_workflow_change_does_not_expand_to_every_domain():
    plan = impact.select([".github/workflows/tenant-go-release.yml"])
    assert plan["domains"] == []
    assert not plan["full"]
    assert not plan["run"]
    assert plan["risky_unknown"] == []


def test_tenant_mcp_change_selects_tenant_runtime():
    plan = impact.select(["platform/apps/tenant-mcp/tool_annotations.go"])
    assert plan["domains"] == ["tenant-runtime"]
    assert plan["run"]
    assert not plan["full"]


def test_unknown_manifest_fails_safe_to_full():
    plan = impact.select(["platform/future-service/deployment.yaml"])
    assert plan["full"]
    assert plan["risky_unknown"] == ["platform/future-service/deployment.yaml"]


def test_docs_only_uses_baseline_only():
    plan = impact.select(["README.md"])
    assert plan["domains"] == []
    assert plan["roots"] == []
    assert set(plan["tests"]) == set(impact.load_catalog()["always_tests"])
    assert not plan["run"]


def test_full_includes_every_test_file():
    plan = impact.select([], full=True)
    assert set(plan["tests"]) == {str(p.relative_to(ROOT)) for p in (ROOT / "tests").glob("test_*.py")}


def test_changed_test_is_not_silently_skipped():
    assert "tests/test_pipeline_archive.py" in impact.select(["tests/test_pipeline_archive.py"])["tests"]


def test_focused_repository_tool_does_not_expand_to_full_regression():
    plan = impact.select([
        "operations/releases/domain_publish.py",
        "tests/test_domain_publication.py",
    ])
    assert plan["focused_suites"] == ["domain-publication"]
    assert plan["domains"] == []
    assert plan["roots"] == []
    assert plan["risky_unknown"] == []
    assert plan["run"]
    assert not plan["full"]
    assert set(plan["tests"]) == set(impact.load_catalog()["always_tests"]) | {"tests/test_domain_publication.py"}


def test_control_plane_workflow_has_one_stable_aggregate_gate():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]

    assert "validate" not in jobs
    assert jobs["control-plane-validate"]["name"] == "control-plane-validate"
    assert jobs["contract"]["if"] == "fromJSON(needs.impact.outputs.jobs)['contract']"
    assert jobs["dependency-review"]["if"] == "fromJSON(needs.impact.outputs.jobs)['dependency-review']"
    assert "&& 2 || 0" in jobs["impact"]["steps"][0]["with"]["fetch-depth"]


def test_digest_promotion_does_not_trigger_overall_regression():
    plan = impact.select(["releases/platform-adapter.yaml", "clusters/re8ch/domain-sources/platform-adapter.yaml", "operations/releases/codenames.yaml"])
    assert plan["domains"] == ["delivery"]
    assert plan["roots"] == ["platform/adapter"]
    assert not plan["full"]


def test_codename_history_alone_fails_closed():
    assert impact.select(["operations/releases/codenames.yaml"])["full"]


def test_focused_source_only_still_runs_tests():
    plan = impact.select(["operations/releases/domain_publish.py"])
    assert plan["focused_suites"] == ["domain-publication"]
    assert plan["run"]
    assert not plan["full"]
    assert plan["domains"] == []
    assert "tests/test_domain_publication.py" in plan["tests"]


def test_fixture_and_parser_consumers_are_all_accounted_for():
    files = {str(p.relative_to(ROOT)) for p in (ROOT / "tests").glob("test_*.py")
             if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "kustomize_documents"
                    for n in ast.walk(ast.parse(p.read_text())))}
    assert files <= set(impact.select(["tests/kustomize_render.py"])["tests"])
    parser = ast.parse((ROOT / "tests/test_native_yaml_loader.py").read_text())
    targets = next(ast.literal_eval(node.value) for node in parser.body if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "TARGETS" for t in node.targets))
    catalog = impact.load_catalog()
    assert set(targets) <= set(catalog["focused_suites"]["native-yaml-contract"]["paths"])
