"""Validate narrowing against input changes, historical edges and real failures."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

from operations.ci import impact, unit_catalog

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(("path", "expected"), [
    ("operations/releases/domain_publish.py", {"domain_publication"}),
    ("operations/releases/allocate_codename.py", {"release_codenames", "domain_publication", "live_domain_sources"}),
    ("operations/flux-lightweight/inventory.py", {"flux_lightweight_inventory", "native_yaml_loader"}),
    ("tests/kustomize_render.py", {"kustomize_render", "flux_controller_ha", "flux_fast_reconcile", "flux_image_controller_retirement", "flux_retirement"}),
])
def test_known_tools_select_all_declared_consumers_without_global_fallback(path, expected):
    plan = impact.select([path])
    assert not plan["full"] and plan["jobs"]["unit"]
    assert set(plan["tests"]) == {f"tests/test_{name}.py" for name in expected | {"incremental_ci"}}
    assert not plan["risky_unknown"]
    report = unit_catalog.describe(plan)
    assert all(report["units"][test]["reasons"][0]["code"] != "explicit-selection" for test in plan["tests"])


@pytest.mark.parametrize("path", [
    "platform/unowned/new.yaml", "operations/releases/unreviewed.py", "tests/new_helper.py",
    "tests/new_helper.json", "tests/test_removed_fixture.py", "operations/ci/impact.py", "tests/conftest.py", "pyproject.toml",
    ".github/actions/setup-native-yaml/action.yml",
])
def test_broad_consumer_inputs_cannot_own_unknown_or_shared_changes(path):
    plan = impact.select([path])
    assert plan["full"]
    assert all(plan["jobs"].values())
    assert set(plan["tests"]) == {str(p.relative_to(ROOT)) for p in (ROOT / "tests").glob("test_*.py")}
    if path == "pyproject.toml":
        record = unit_catalog.describe(plan)["units"]["tests/test_incremental_ci.py"]
        assert record["reasons"][0]["paths"] == [path]
        assert record["reasons"][0]["reason"] == "shared-input"


def test_scanned_directory_additions_and_deletions_keep_inventory_checks():
    # Selection is based on paths, so deletion and as-yet-unwritten files match too.
    plan = impact.select(["platform/advanced-fabric/new-or-removed.yaml"])
    assert not plan["full"]
    assert {"tests/test_flux_lightweight_inventory.py", "tests/test_helmrelease_inventory.py",
            "tests/test_git_k8s_manifests.py"} <= set(plan["tests"])
    assert plan["checks"]["helm_inventory"]




@pytest.fixture
def graph(tmp_path, monkeypatch):
    catalog = copy.deepcopy(impact.load_catalog())
    catalog.update({"external_inputs": {}, "always_tests": ["tests/test_incremental_ci.py"],
                    "validation_units": {}, "unit_defaults": {"helpers": [], "environment": []},
                    "focused_suites": {"fixtures": {"paths": ["fixtures/**"], "tests": ["tests/test_dependency.py"]}},
                    "domains": {name: {"paths": [f"platform/{name}/**"], "roots": [f"platform/{name}"],
                                       "tests": [f"tests/test_{name}.py"]} for name in ("a", "b")}})
    def write(path, value):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(value) if not isinstance(value, str) else value)
    write("operations/ci/domain-catalog.yaml", catalog)
    for name in ("a", "b", "dependency", "incremental_ci"):
        write(f"tests/test_{name}.py", "def test_ok(): pass\n")
    write("platform/a/kustomization.yaml", {"resources": ["../../fixtures/base"]})
    write("platform/b/kustomization.yaml", {"resources": []})
    write("fixtures/base/kustomization.yaml", {"resources": ["../old.yaml"]})
    write("fixtures/old.yaml", {"kind": "ConfigMap"})
    monkeypatch.setattr(impact, "ROOT", tmp_path)
    monkeypatch.setattr(impact, "CATALOG", tmp_path / "operations/ci/domain-catalog.yaml")
    return tmp_path, write


def test_base_and_head_graph_union_includes_removed_and_new_edges(graph):
    root, write = graph
    old = {"platform/a": {"resources": ["../../fixtures/base"]},
           "fixtures/base": {"resources": ["../old.yaml"]}}
    write("platform/a/kustomization.yaml", {"resources": []})
    write("platform/b/kustomization.yaml", {"resources": ["../../fixtures/new.yaml"]})
    (root / "fixtures/old.yaml").unlink()
    write("fixtures/new.yaml", {"kind": "ConfigMap"})
    plan = impact.select(["fixtures/old.yaml", "fixtures/new.yaml"], base_definitions=old)
    assert not plan["full"]
    assert plan["domains"] == ["a", "b"]
    assert plan["dependency_reasons"]["platform/a"] == {"head": False, "base": True}
    assert plan["dependency_reasons"]["platform/b"] == {"head": True, "base": False}
    assert {"tests/test_a.py", "tests/test_b.py"} <= set(plan["tests"])
    report = unit_catalog.describe(plan, root)
    assert any(r["code"] == "base-head-dependencies" for r in report["units"]["tests/test_a.py"]["reasons"])


def test_git_snapshot_reads_historical_content_and_diff_preserves_both_rename_paths(graph):
    root, write = graph
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
        "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture")
    base = git("rev-parse", "HEAD")
    write("platform/a/kustomization.yaml", {"resources": []})
    moved = "fixtures/new name\n测试.yaml"
    (root / "fixtures/old.yaml").rename(root / moved)
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
        "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture change")
    assert impact.definitions_at(base)["platform/a"]["resources"] == ["../../fixtures/base"]
    assert {"fixtures/old.yaml", moved} <= set(impact.changed_paths(base, "HEAD"))


@pytest.mark.parametrize("config", [
    {"patchesJson6902": [{"path": "../../fixtures/input.yaml"}]},
    {"replacements": [{"path": "../../fixtures/input.yaml"}]},
    {"openapi": {"path": "../../fixtures/input.yaml"}},
    {"configMapGenerator": [{"files": ["value=../../fixtures/input.yaml"]}]},
    {"secretGenerator": [{"envs": ["../../fixtures/input.yaml"]}]},
    {"configurations": ["../../fixtures/input.yaml"]},
])
def test_non_resource_file_dependencies_remain_visible(graph, config):
    _, write = graph
    write("platform/a/kustomization.yaml", config)
    assert impact.select(["fixtures/input.yaml"])["domains"] == ["a"]


def test_shared_and_cycles_retain_every_consumer(graph):
    _, write = graph
    write("platform/b/kustomization.yaml", {"resources": ["../../fixtures/base"]})
    write("fixtures/base/kustomization.yaml", {"resources": ["../../platform/a", "../old.yaml"]})
    assert impact.select(["fixtures/old.yaml"])["domains"] == ["a", "b"]


def test_current_external_render_dependency_selects_test_consumer(graph):
    root, write = graph
    catalog = yaml.safe_load((root / "operations/ci/domain-catalog.yaml").read_text())
    catalog["validation_units"]["tests/test_dependency.py"] = {
        "sources": [], "data": [], "helpers": [], "tools": [], "reuse": "candidate", "roots": ["platform/a"]}
    write("operations/ci/domain-catalog.yaml", catalog)
    plan = impact.select(["fixtures/old.yaml"])
    assert "platform/a" in plan["unit_roots"]["tests/test_dependency.py"]


def test_missing_base_falls_back_to_full_with_reason(tmp_path):
    result = subprocess.run([sys.executable, "operations/ci/impact.py", "--base", "refs/heads/nonexistent-fixture-ref"],
                            cwd=ROOT, env={**os.environ, "GITHUB_OUTPUT": str(tmp_path / "output")},
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["full"] and all(plan["jobs"].values())
    assert plan["fallback_reason"] == "base-dependencies-unavailable"


@pytest.mark.parametrize("args", [
    ["--path", "operations/releases/allocate_codename.py"],
    ["--base", "HEAD", "--head", "HEAD"],
])
def test_github_outputs_match_printed_plan(tmp_path, args):
    output = tmp_path / "github-output"
    result = subprocess.run([sys.executable, "operations/ci/impact.py", *args], cwd=ROOT,
                            env={**os.environ, "GITHUB_OUTPUT": str(output)}, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    if "--base" in args:
        assert plan["dependency_base"] == subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        assert plan["fallback_reason"] is None
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert json.loads(values["plan"]) == plan
    for key in ("domains", "tests", "roots", "submodules", "jobs", "checks", "run", "full"):
        assert json.loads(values[key]) == plan[key]


def test_unsupported_dependency_fields_cli_falls_back_to_full(graph):
    root, write = graph
    for name in ("impact.py", "unit_catalog.py", "render_evidence.py", "input_snapshot.py"):
        shutil.copyfile(ROOT / "operations/ci" / name, root / "operations/ci" / name)
    write("platform/a/kustomization.yaml", {"generators": ["../../fixtures/plugin.yaml"]})
    result = subprocess.run([sys.executable, "operations/ci/impact.py", "--path", "fixtures/plugin.yaml"],
                            cwd=root, env={**os.environ, "GITHUB_OUTPUT": str(root / "output")},
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["full"] and all(plan["jobs"].values())
    assert plan["fallback_reason"] == "dependencies-unreadable"


def test_selected_real_consumer_detects_a_broken_allocator(tmp_path):
    test = "tests/test_release_codenames.py"
    source = "operations/releases/allocate_codename.py"
    assert test in impact.select([source])["tests"]
    for path in (test, source, "operations/ci/input_snapshot.py", "operations/releases/codenames.yaml", "operations/ci/domain-catalog.yaml"):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, target)
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    for name in ("PYTEST_ADDOPTS", "UNIT_PLAN", "GITHUB_STEP_SUMMARY"):
        env.pop(name, None)
    args = [sys.executable, "-m", "pytest", "--color=no", "-q", test]
    good = subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert good.returncode == 0, good.stdout + good.stderr
    target = tmp_path / source
    lines = target.read_text().splitlines()
    target.write_text("\n".join('    return "broken"' if line.startswith("    return base if") else line for line in lines) + "\n")
    broken = subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert broken.returncode == 1
    assert "2 failed, 1 passed" in broken.stdout


def test_file_plan_omits_large_actions_output(tmp_path):
    import os
    output = tmp_path / 'github-output'
    plan_file = tmp_path / 'plan.json'
    result = subprocess.run([sys.executable, str(ROOT / 'operations/ci/impact.py'),
                             '--path', 'README.md', '--full', '--plan-file', str(plan_file)],
                            cwd=ROOT, env={**os.environ, 'GITHUB_OUTPUT': str(output)},
                            capture_output=True, text=True, check=True)
    plan = json.loads(result.stdout)
    assert json.loads(plan_file.read_text()) == plan
    values = dict(line.split('=', 1) for line in output.read_text().splitlines())
    assert 'plan' not in values
    assert json.loads(values['jobs']) == plan['jobs']
