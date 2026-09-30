import importlib.util
import base64
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from runtime import CloudPlatform, Identity, Inventory, OperationError, OperationRunner

ROOT = Path(__file__).parents[1]


def resources():
    return list(yaml.safe_load_all((ROOT / "platform/cloud-platform/runtime.yaml").read_text()))


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
    assert by_name["kubectl.exec"]["inputSchema"]["required"] == ["argv"]
    assert "workspace.sync" in names
    assert "workspace.status" in names


def test_dispatch_rechecks_authorization():
    with pytest.raises(OperationError) as caught:
        server.dispatch(service(), "node.ssh.exec", {"name": "restricted-node-6dc3f140f9", "argv": ["hostname"]}, Identity("observer", ()))
    assert caught.value.code == "AUTHORIZATION_DENIED"
    with pytest.raises(OperationError):
        server.dispatch(service(), "kubectl.exec", {"argv": ["get", "nodes"]}, Identity("observer", ()))


def test_admin_generic_kubectl_and_inventory_ssh():
    calls = []

    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    runtime = CloudPlatform(Inventory([ROOT / "inventory/nodes.yaml"]), OperationRunner(execute), kubeconfig="x", ssh_config="y")
    admin = Identity("admin", (server.ADMIN_GROUP,))
    assert server.dispatch(runtime, "kubectl.exec", {"argv": ["rollout", "status", "deployment/example"], "timeout": 45}, admin)["exitCode"] == 0
    assert calls[0][0] == ["kubectl", "--kubeconfig", "x", "rollout", "status", "deployment/example"]
    assert calls[0][1]["timeout"] == 45
    assert server.dispatch(runtime, "node.ssh.exec", {"name": "restricted-node-6dc3f140f9", "argv": ["hostname"]}, admin)["exitCode"] == 0
    assert calls[1][0] == ["ssh", "-F", "y", "--", "restricted-node-6dc3f140f9", "--", "hostname"]


def test_observer_can_see_workspace_status_but_not_mutating_sync():
    names = {tool["name"] for tool in server.tool_list(Identity("observer", ())) }
    assert "workspace.status" in names
    assert "workspace.sync" not in names
    assert {"workspace.context", "workspace.memory-audit"} <= names


def test_user_token_must_target_cloud_platform_client(monkeypatch):
    class Userinfo:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return b'{"sub":"admin","groups":["re8ch:k8s-admins"]}'

    monkeypatch.setattr(server.urllib.request, "urlopen", lambda *_args, **_kwargs: Userinfo())

    def token(audience):
        payload = base64.urlsafe_b64encode(json.dumps({"aud": audience}).encode()).decode().rstrip("=")
        return f"header.{payload}.signature"

    assert server.authenticate("Bearer " + token("another-client")) is None
    identity = server.authenticate("Bearer " + token(server.OAUTH_AUDIENCE))
    assert identity.subject == "admin"
    assert server.WRITE_TOOLS <= {tool["name"] for tool in server.tool_list(identity)}


def test_admin_route_and_oauth_resource_match():
    route = next(item for item in resources() if item["kind"] == "HTTPRoute")
    paths = {match["path"]["value"] for rule in route["spec"]["rules"] for match in rule["matches"]}
    assert paths == {"/cloud/mcp", "/.well-known/oauth-protected-resource/cloud/mcp"}
    assert route["spec"]["hostnames"] == ["operate.tools.re8ch.com"]
    assert server.PUBLIC_MCP_URL == "https://operate.tools.re8ch.com/cloud/mcp"
