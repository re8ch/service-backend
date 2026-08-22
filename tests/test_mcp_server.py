import importlib.util
from pathlib import Path

import pytest

from runtime import CloudPlatform, Identity, Inventory, OperationError, OperationRunner

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("cloud_server", ROOT / "operations/cloud-platform/server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def service():
    return CloudPlatform(Inventory([ROOT / "inventory/nodes.yaml"]), OperationRunner(), kubeconfig="x", ssh_config="y")


def test_observer_catalog_has_no_mutating_tools():
    names = {tool["name"] for tool in server.tool_list(Identity("observer", ("re8ch:observers",)))}
    assert not names & server.WRITE_TOOLS
    assert "kube.events.query" in names


def test_admin_catalog_has_mutating_tools():
    names = {tool["name"] for tool in server.tool_list(Identity("admin", (server.ADMIN_GROUP,)))}
    assert server.WRITE_TOOLS <= names
    by_name = {tool["name"]: tool for tool in server.tool_list(Identity("admin", (server.ADMIN_GROUP,)))}
    assert by_name["node.ssh.exec"]["inputSchema"]["required"] == ["name", "argv"]
    assert "workspace.sync" in names
    assert "workspace.materialize" in names


def test_dispatch_rechecks_authorization():
    with pytest.raises(OperationError) as caught:
        server.dispatch(service(), "node.ssh.exec", {"name": "restricted-node-6dc3f140f9", "argv": ["hostname"]}, Identity("observer", ()))
    assert caught.value.code == "AUTHORIZATION_DENIED"


def test_observer_can_see_workspace_status_but_not_materialize():
    names = {tool["name"] for tool in server.tool_list(Identity("observer", ())) }
    assert "workspace.status" in names
    assert "workspace.materialize" not in names
