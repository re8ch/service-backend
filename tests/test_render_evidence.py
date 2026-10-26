import importlib.util
import json
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("render_evidence", ROOT / "operations/ci/render_evidence.py")
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "ROOT", tmp_path)
    (tmp_path / "app").mkdir()
    (tmp_path / "shared").mkdir()
    (tmp_path / "app/kustomization.yaml").write_text("resources: [../shared/config.yaml]\n")
    (tmp_path / "shared/config.yaml").write_text("apiVersion: v1\nkind: ConfigMap\nmetadata: {name: example}\n")
    return tmp_path


def test_dependency_changes_invalidate_but_unrelated_changes_do_not(tree):
    key = evidence.input_key("app", {"kubectl": "1"})
    assert key
    (tree / "unrelated.txt").write_text("unrelated commit")
    assert evidence.input_key("app", {"kubectl": "1"}) == key
    (tree / "shared/config.yaml").write_text("changed")
    assert evidence.input_key("app", {"kubectl": "1"}) != key
    assert evidence.input_key("app", {"kubectl": "2"}) != evidence.input_key("app", {"kubectl": "1"})


@pytest.mark.parametrize("config", [
    {"resources": ["https://example.com/base"]},
    {"resources": ["github.com/org/repo//base?ref=main"]},
    {"helmCharts": [{"name": "remote"}]},
    {"generators": ["plugin.yaml"]},
    {"newInputField": "unknown"},
    {"resources": ["missing.yaml"]},
    {"resources": ["../../outside.yaml"]},
])
def test_unbounded_inputs_never_reuse(tree, config):
    (tree / "app/kustomization.yaml").write_text(yaml.safe_dump(config))
    assert evidence.input_key("app", {}) is None


def test_symlink_disables_reuse(tree):
    (tree / "app/linked.yaml").symlink_to(tree / "shared/config.yaml")
    (tree / "app/kustomization.yaml").write_text("resources: [linked.yaml]\n")
    assert evidence.input_key("app", {}) is None


def test_nested_generator_patch_and_replacement_inputs(tree):
    (tree / "app/base").mkdir()
    (tree / "app/base/kustomization.yaml").write_text("resources: [../../shared/config.yaml]\n")
    config = {"resources": ["base"], "configMapGenerator": [{"name": "settings", "files": ["key=settings.txt"], "envs": ["env.txt"]}], "patches": [{"path": "patch.yaml"}], "replacements": [{"path": "replacement.yaml"}]}
    (tree / "app/kustomization.yaml").write_text(yaml.safe_dump(config))
    for name in ("settings.txt", "env.txt", "patch.yaml", "replacement.yaml"):
        (tree / "app" / name).write_text("original")
    for name in ("settings.txt", "env.txt", "patch.yaml", "replacement.yaml", "base/kustomization.yaml"):
        old = evidence.input_key("app", {})
        path = tree / "app" / name
        path.write_text(path.read_text() + "\n# changed\n")
        assert evidence.input_key("app", {}) != old


def test_exact_success_reused_and_corruption_reexecuted(tree, monkeypatch):
    calls = []
    def render(*args, **kwargs):
        calls.append(args)
        return "rendered manifest\n"
    monkeypatch.setattr(evidence.subprocess, "check_output", render)
    baseline = evidence.render_roots(["app"], {}, {})
    assert baseline["executed"] == ["app"]
    reused = evidence.render_roots(["app"], baseline, {})
    assert reused["reused"] == ["app"] and len(calls) == 1
    baseline["records"]["app"]["rendered"] = "corrupted"
    assert evidence.render_roots(["app"], baseline, {})["executed"] == ["app"]
    assert len(calls) == 2


def test_failed_render_does_not_return_evidence(tree, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "kubectl")
    monkeypatch.setattr(evidence.subprocess, "check_output", fail)
    with pytest.raises(subprocess.CalledProcessError):
        evidence.render_roots(["app"], {}, {})


def test_only_successful_main_regression_is_trusted():
    run = {"head_branch": "main", "conclusion": "success", "event": "schedule", "path": ".github/workflows/full-regression.yml", "head_repository": {"full_name": "owner/repo"}}
    assert evidence.trusted_run(run, "owner/repo")
    for key, value in [("head_branch", "feature"), ("conclusion", "failure"), ("event", "pull_request"), ("path", ".github/workflows/untrusted.yml"), ("head_repository", {"full_name": "fork/repo"})]:
        assert not evidence.trusted_run({**run, key: value}, "owner/repo")


def test_baseline_lookup_falls_back_without_gh(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    def missing(*args, **kwargs):
        raise FileNotFoundError("gh")
    monkeypatch.setattr(evidence.subprocess, "check_output", missing)
    assert evidence.baseline_run() == ""


def test_workflows_publish_only_main_and_force_full_ignores_evidence():
    full = yaml.safe_load((ROOT / ".github/workflows/full-regression.yml").read_text())
    upload = full["jobs"]["regression"]["steps"][-1]
    assert "refs/heads/main" in upload["if"]
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = ci["jobs"]["render"]["steps"]
    baseline = next(step for step in steps if step.get("id") == "baseline")
    assert baseline["if"] == "needs.impact.outputs.full != 'true'"
    assert not any("upload-artifact" in step.get("uses", "") for step in steps)
