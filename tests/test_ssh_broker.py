import importlib.util
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("ssh_broker_runtime", ROOT / "operations/ssh-broker/runtime.py")
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


def registry(tmp_path):
    path = tmp_path / "targets.yaml"
    path.write_text("""apiVersion: operations.re8ch.com/v1alpha1
kind: InfrastructureTargetList
items:
  - metadata: {name: node-one}
    spec:
      transport: ssh
      address: legacy-alias
      cluster: {fabric: {acceleratedIP: 10.250.0.9, internalIP: 10.181.22.9}}
""")
    return runtime.TargetRegistry(path)


def test_registry_uses_independent_management_fabric_without_kubernetes(tmp_path):
    target, route = registry(tmp_path).route("node-one")
    assert target == "10.250.0.9"
    assert route == "accelerated-management"


def test_broker_uses_managed_identity_and_preserves_exact_argv(tmp_path):
    calls = []
    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "node-one\n", "")
    broker = runtime.SSHBroker(registry(tmp_path), ssh_config="/managed/ssh-config", executor=execute)
    result = broker.execute("node-one", ["printf", "%s", "a b"], actor="admin-subject")
    assert calls[0][0][:7] == ["ssh", "-F", "/managed/ssh-config", "-l", "re8ch-ai", "--", "10.250.0.9"]
    assert calls[0][0][7] == "printf %s 'a b'"
    assert result["auditRef"].startswith("audit://ssh-broker/")
    assert result["actor"] == "admin-subject"
    assert len(result["commandHash"]) == 64
    assert "printf" not in result["commandHash"]
    assert "10.250.0.9" not in str(result)


def test_unknown_target_fails_closed(tmp_path):
    with pytest.raises(runtime.BrokerError) as caught:
        registry(tmp_path).route("missing")
    assert caught.value.code == "TARGET_NOT_FOUND"
