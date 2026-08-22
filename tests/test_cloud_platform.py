import json
import subprocess
from pathlib import Path

import pytest

from runtime import CloudPlatform, Inventory, OperationError, OperationRunner, redact


ROOT = Path(__file__).parents[1]


def completed(stdout="{}", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, "")


def test_inventory_contains_all_sixteen_k3s_nodes():
    inventory = Inventory([ROOT / "inventory/nodes.yaml"])
    assert len(inventory.targets) == 16
    assert {"restricted-node-6dc3f140f9", "overseas-kl", "overseas-la", "suxunji", "vm-20-12-ubuntu"} <= set(inventory.targets)


def test_redaction_is_recursive():
    assert redact({"nested": {"token": "never", "ok": 1}}) == {"nested": {"token": "[REDACTED]", "ok": 1}}


def test_secret_values_are_never_returned():
    payload = {"items": [{"metadata": {"name": "x"}, "data": {"password": "eA=="}}]}
    runner = OperationRunner(lambda *args, **kwargs: completed(json.dumps(payload)))
    service = CloudPlatform(Inventory([ROOT / "inventory/nodes.yaml"]), runner, kubeconfig="x", ssh_config="y")
    result = service.kube_simple("secrets", namespace="default")
    assert "data" not in result["result"]["items"][0]
    assert "eA==" not in json.dumps(result)


def test_ssh_rejects_undeclared_command():
    service = CloudPlatform(Inventory([ROOT / "inventory/nodes.yaml"]), OperationRunner(), kubeconfig="x", ssh_config="y")
    with pytest.raises(OperationError, match="bash"):
        service.node_ssh_exec("restricted-node-6dc3f140f9", ["bash", "-c", "id"])


def test_apply_rejects_inline_secret_material():
    service = CloudPlatform(Inventory([ROOT / "inventory/nodes.yaml"]), OperationRunner(), kubeconfig="x", ssh_config="y")
    with pytest.raises(OperationError) as caught:
        service.kube_resource("apply", "secret", manifest={"data": {"x": "eA=="}})
    assert caught.value.code == "SECRET_VALUE_FORBIDDEN"
