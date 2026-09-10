import importlib.util
import pathlib
import subprocess
import sys
from unittest import mock


PATH = pathlib.Path(__file__).parents[1] / "operations/scripts/runtime-anatomy-canary.py"
SPEC = importlib.util.spec_from_file_location("runtime_anatomy_canary", PATH)
CANARY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CANARY)


def completed(code, stdout="", stderr=""):
    return subprocess.CompletedProcess(["kubectl"], code, stdout, stderr)


def test_wait_exists_tolerates_gitops_creation_delay():
    responses = [completed(1, stderr="NotFound"), completed(0, stdout="configmap/scope\n")]
    with mock.patch.object(CANARY.subprocess, "run", side_effect=responses), \
         mock.patch.object(CANARY.time, "sleep"):
        assert CANARY.wait_exists("runtime-anatomy", "configmap", "scope", timeout=10) == "configmap/scope"


def test_kubectl_error_includes_actionable_stderr():
    with mock.patch.object(CANARY.subprocess, "run", return_value=completed(1, stderr="forbidden")):
        try:
            CANARY.kubectl("get", "pods")
        except RuntimeError as exc:
            assert "forbidden" in str(exc)
            assert "kubectl get pods failed" in str(exc)
        else:
            raise AssertionError("kubectl failure was swallowed")


def test_unhandled_failure_writes_evidence(tmp_path):
    evidence = {"checks": {}}
    output = tmp_path / "evidence.json"
    previous = sys.excepthook
    with mock.patch.object(sys, "__excepthook__"):
        CANARY.install_failure_evidence(evidence, output)
        sys.excepthook(RuntimeError, RuntimeError("not ready"), None)
    sys.excepthook = previous
    assert '"result": "failed"' in output.read_text()
    assert "not ready" in output.read_text()
