import importlib.util
import json
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("workspace", ROOT / "operations/workspace-controller/workspace.py")
workspace = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = workspace
spec.loader.exec_module(workspace)


def test_registry_is_valid_and_has_expected_catalogs():
    registry = workspace.Registry(ROOT)
    registry.validate()
    assert len(registry.modules) >= 40
    assert {"cloud-functions", "service-backend", "jjklive", "ledger", "qianwen-app", "artchais-web"} <= registry.modules.keys()


def test_collaboration_modules_never_use_owned_write_policy():
    registry = workspace.Registry(ROOT)
    for module in registry.modules.values():
        if module.ownership == "collaboration":
            assert module.write_policy in {"authorized-upstream-pr", "patch-only", "read-only"}


def test_lock_covers_every_published_module():
    registry = workspace.Registry(ROOT)
    lock = yaml.safe_load((ROOT / "workspace/workspace.lock.yaml").read_text())
    expected = {name for name, module in registry.modules.items() if module.source_kind == "submodule"}
    assert set(lock["modules"]) == expected
    assert all(len(item["commit"]) == 40 for item in lock["modules"].values())


def test_generated_indexes_are_current():
    expected = workspace.Controller(ROOT).build_indexes()
    for name, content in expected.items():
        assert json.loads((ROOT / "indexes" / f"{name}.json").read_text()) == content


def test_every_non_root_module_is_a_real_gitlink():
    controller = workspace.Controller(ROOT)
    for module in controller.registry.modules.values():
        if module.source_kind == "root":
            assert module.name == "service-backend"
            continue
        source = controller._module_source(module)
        entry = workspace.git(ROOT, "ls-files", "--stage", source)
        assert entry.startswith("160000 "), source


def test_every_owned_module_has_one_account_and_workspace():
    registry = workspace.Registry(ROOT)
    documents = list(yaml.safe_load_all((ROOT / "platform/git-workspaces/project-accounts.yaml").read_text()))
    accounts = [item for item in documents if item["kind"] == "GitAccount"]
    workspaces = [item for item in documents if item["kind"] == "GitWorkspace"]
    owned = {module.name for module in registry.modules.values() if module.ownership == "owned"}
    assert {item["spec"]["project"] for item in accounts} == owned
    assert {item["spec"]["projectRef"] for item in workspaces} == owned
    assert len(accounts) == len(workspaces) == len(owned)
    assert len({item["spec"]["accountRef"] for item in workspaces}) == len(owned)


def test_repository_key_normalizes_ssh_and_https():
    assert workspace.repository_key("git@github.com:re8ch/example.git") == workspace.repository_key("https://github.com/re8ch/example.git")


def test_every_module_has_agent_memory_and_context_is_queryable():
    controller = workspace.Controller(ROOT)
    audit = controller.memory_audit()["result"]
    assert audit["complete"] is True
    assert len(audit["modules"]) == len(controller.registry.modules)
    context = controller.context("cloud-functions")["result"][0]
    assert context["memoryAvailable"] is True
    assert "Cloud Functions" in context["memory"]
