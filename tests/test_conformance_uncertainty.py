import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

BASE = Path(__file__).parents[1] / "operations/api-conformance"


def module(name, monkeypatch):
    monkeypatch.syspath_prepend(str(BASE))
    spec = importlib.util.spec_from_file_location("uncertainty_" + name, BASE / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.mark.parametrize("failure", ["setup_lost_response", "timeout", "reversal_failure", "cleanup_failure"])
def test_uncertain_run_keeps_sanitized_compensation_result(monkeypatch, capsys, failure):
    runner = module("runner", monkeypatch)
    target = {"baseUrl": "https://example.invalid", "schema": {"sha256": "a" * 64},
              "operations": {"mutationOperationIds": ["create"]}}
    monkeypatch.setenv("TARGET_NAME", "test")
    monkeypatch.setenv("RUN_ID", "run")
    monkeypatch.setenv("RUN_MODE", "mutation")
    monkeypatch.setenv("TARGET_JSON", json.dumps(target))
    monkeypatch.setattr(runner, "validate_target", lambda value: value)
    monkeypatch.setattr(runner, "fetch_schema", lambda value: b"{}")
    monkeypatch.setattr(runner, "schema_digest", lambda value: "a" * 64)
    monkeypatch.setattr(runner, "validate_document", lambda *a: {})
    called = []
    def steps(target, key, variables):
        called.append(key)
        if (key == "setup" and failure == "setup_lost_response") or failure == key + "_failure":
            raise runner.UncertainMutation("sensitive error body must never appear")
        return "passed"
    monkeypatch.setattr(runner, "run_steps", steps)
    def execute(*args, **kwargs):
        raise subprocess.TimeoutExpired("command containing credentials", 1)
    monkeypatch.setattr(runner.subprocess, "run", execute)
    assert runner.main() == 1
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["status"] == "failed"
    assert result["lifecycle"]["test"] == "failed"
    assert called[-2:] == ["reversal", "cleanup"]
    assert result["lifecycle"]["reversal"] == ("failed" if failure == "reversal_failure" else "passed")
    assert result["lifecycle"]["cleanup"] == ("failed" if failure == "cleanup_failure" else "passed")
    assert "sensitive error" not in output and "credentials" not in output


@pytest.mark.parametrize("log", ["not json", '{"status":"failed","lifecycle":{"cleanup":"passed","reversal":"passed"}}'])
def test_failed_mutation_blocks_even_if_cleanup_reports_success(monkeypatch, log):
    controller = module("controller", monkeypatch)
    run = {"metadata": {"name": "run"}, "spec": {"targetRef": "target", "mode": "mutation"}, "status": {"phase": "Running"}}
    target = {"metadata": {"name": "target"}, "spec": {}}
    monkeypatch.setattr(controller, "kube_request", lambda *a: {"items": [{"metadata": {"name": "run"}, "status": {"failed": 1}}]})
    monkeypatch.setattr(controller, "list_resource", lambda *a: [run])
    monkeypatch.setattr(controller, "pod_log", lambda *a: log)
    monkeypatch.setattr(controller, "publish", lambda *a: None)
    statuses = []
    monkeypatch.setattr(controller, "patch_status", lambda resource, name, status: statuses.append((resource, status)))
    controller.collect_runs({"target": target})
    assert statuses[-1][1]["mutationBlocked"] is True
    assert statuses[-1][1]["consecutivePasses"] == 0
