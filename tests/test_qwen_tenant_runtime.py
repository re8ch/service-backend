import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def documents():
    path = ROOT / "platform/qwen-tenant-runtime/runtime.yaml"
    return [item for item in yaml.safe_load_all(path.read_text()) if item]


def find(kind, name, namespace=None):
    for item in documents():
        metadata = item.get("metadata", {})
        if item.get("kind") == kind and metadata.get("name") == name:
            if namespace is None or metadata.get("namespace") == namespace:
                return item
    raise AssertionError(f"missing {kind} {namespace or '*'} / {name}")


def test_qwen_vmcp_uses_shared_session_storage():
    vmcp = find("VirtualMCPServer", "qwen-tenant-vmcp", "toolhive-system")
    session = vmcp["spec"]["sessionStorage"]
    assert vmcp["spec"]["replicas"] == 2
    assert session["provider"] == "redis"
    assert session["db"] == 3
    assert session["keyPrefix"] == "thv:vmcp:qwen:"
    assert session["passwordRef"] == {"name": "toolhive-auth-redis", "key": "password"}


def test_qwen_plugin_is_limited_to_qwen_namespaces_and_never_reads_secrets():
    path = ROOT / "platform/qwen-tenant-runtime/plugins/qwen-tenant-runtime.json"
    plugin = json.loads(path.read_text())
    scopes = plugin["spec"]["policy"]["resourceScopes"]
    assert scopes
    for scope in scopes:
        assert set(scope["namespaces"]) <= {"qwen", "tenant-qwen-runtime"}
        assert scope["resource"] != "secrets"


def test_headlamp_identity_gets_navigation_and_namespace_roles_only():
    cluster_binding = find(
        "ClusterRoleBinding", "qwen-headlamp-navigation-github-tianruoyou2022"
    )
    subjects = {item["name"] for item in cluster_binding["subjects"]}
    assert subjects == {"oidc:85153954@qq.com", "oidc:tianruoyou2022"}
    assert cluster_binding["roleRef"]["name"] == "qwen-headlamp-navigation"

    for namespace in ("qwen", "tenant-qwen-runtime"):
        binding = find(
            "RoleBinding", "qwen-headlamp-operator-github-tianruoyou2022", namespace
        )
        assert binding["roleRef"]["kind"] == "Role"
        assert binding["roleRef"]["name"] == "qwen-tenant-runtime"
