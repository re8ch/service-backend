import importlib.util
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("impact", ROOT / "operations/ci/impact.py")
impact = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(impact)


def test_every_kustomize_root_has_one_owner():
    impact.load_catalog()


def test_network_change_is_incremental():
    plan = impact.select(["platform/advanced-fabric/dns/kustomization.yaml"])
    assert plan["domains"] == ["networking"]
    assert not plan["full"]
    assert "platform/observability/production" not in plan["roots"]
    assert plan["roots"] == ["platform/advanced-fabric"]


def test_shared_ci_change_fails_safe_to_full():
    plan = impact.select(["operations/ci/impact.py"])
    assert plan["full"]
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


def test_control_plane_workflow_has_one_stable_aggregate_gate():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]

    assert "validate" not in jobs
    assert jobs["control-plane-validate"]["name"] == "control-plane-validate"
    assert jobs["contract"]["if"] == "needs.impact.outputs.run == 'true'"
    assert jobs["dependency-review"]["if"] == "needs.impact.outputs.full == 'true'"
    assert "&& 2 || 0" in jobs["impact"]["steps"][0]["with"]["fetch-depth"]


def test_digest_promotion_does_not_trigger_overall_regression():
    plan = impact.select(["releases/platform-adapter.yaml", "clusters/re8ch/domain-sources/platform-adapter.yaml", "operations/releases/codenames.yaml"])
    assert plan["domains"] == ["delivery"]
    assert plan["roots"] == ["platform/adapter"]
    assert not plan["full"]


def test_codename_history_alone_fails_closed():
    assert impact.select(["operations/releases/codenames.yaml"])["full"]
