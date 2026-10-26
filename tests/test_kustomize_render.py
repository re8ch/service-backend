import subprocess

import pytest
import yaml

import kustomize_render


@pytest.fixture
def render(tmp_path, monkeypatch):
    base = tmp_path / "root"
    base.mkdir()
    (base / "kustomization.yaml").write_text("resources: [object.yaml]\n")
    (base / "object.yaml").write_text("apiVersion: v1\nkind: ConfigMap\nmetadata: {name: sample}\n")
    for tool in ("kustomize", "kubectl"):
        (tmp_path / tool).write_text(tool)
    monkeypatch.setattr(kustomize_render.shutil, "which", lambda tool: str(tmp_path / tool))
    calls = []
    def build(command, **kwargs):
        calls.append(command)
        return "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: sample}\n"
    monkeypatch.setattr(kustomize_render.subprocess, "check_output", build)
    return kustomize_render.KustomizeRender(tmp_path), calls, tmp_path


def test_identical_inputs_reuse_render_and_return_independent_objects(render):
    renderer, calls, _ = render
    first = renderer("root")
    first[0]["metadata"]["name"] = "mutated"
    assert renderer("root")[0]["metadata"]["name"] == "sample"
    assert len(calls) == 1
    assert (renderer.executed, renderer.reused) == (1, 1)


def test_changed_dependency_invalidates_but_unrelated_file_does_not(render):
    renderer, calls, root = render
    renderer("root")
    (root / "unrelated.txt").write_text("other module")
    renderer("root")
    assert len(calls) == 1
    (root / "root/object.yaml").write_text("apiVersion: v1\nkind: ConfigMap\nmetadata: {name: changed}\n")
    renderer("root")
    assert len(calls) == 2
    (root / "root/object.yaml").unlink()
    renderer("root")
    assert len(calls) == 3  # Missing inputs cannot reuse an earlier success.


def test_renderer_and_binary_changes_do_not_share_evidence(render):
    renderer, calls, root = render
    renderer("root")
    renderer("root", kubectl=True)
    assert [call[1] for call in calls] == ["build", "kustomize"]
    (root / "kustomize").write_text("replacement binary")
    renderer("root")
    assert len(calls) == 3


def test_default_fallback_reuses_explicit_kubectl(render, monkeypatch):
    renderer, calls, root = render
    monkeypatch.setattr(kustomize_render.shutil, "which", lambda tool: None if tool == "kustomize" else str(root / tool))
    renderer("root")
    renderer("root", kubectl=True)
    assert len(calls) == 1 and calls[0][1] == "kustomize"


def test_unbounded_inputs_always_render(render):
    renderer, calls, root = render
    (root / "root/kustomization.yaml").write_text("resources: [https://example.invalid/base]\n")
    renderer("root")
    renderer("root")
    assert len(calls) == 2
    assert not renderer.cache


@pytest.mark.parametrize("output", [None, "broken: [yaml"])
def test_failed_render_or_parse_is_not_cached(render, monkeypatch, output):
    renderer, _, _ = render
    calls = []
    def fail_then_succeed(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            if output is None:
                raise subprocess.CalledProcessError(1, "kustomize")
            return output
        return "kind: ConfigMap\n"
    monkeypatch.setattr(kustomize_render.subprocess, "check_output", fail_then_succeed)
    with pytest.raises((subprocess.CalledProcessError, yaml.YAMLError)):
        renderer("root")
    assert not renderer.cache
    assert renderer("root") == [{"kind": "ConfigMap"}]
    assert len(calls) == 2


def test_sessions_do_not_reuse_previous_results(render):
    renderer, calls, root = render
    renderer("root")
    kustomize_render.KustomizeRender(root)("root")
    assert len(calls) == 2
