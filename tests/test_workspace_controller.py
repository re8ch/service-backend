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
    assert set(lock["modules"]) == set(registry.modules)
    assert all(len(item["commit"]) == 40 for item in lock["modules"].values())


def test_generated_indexes_are_current():
    expected = workspace.Controller(ROOT).build_indexes()
    for name, content in expected.items():
        assert json.loads((ROOT / "indexes" / f"{name}.json").read_text()) == content


def test_materialize_defaults_to_dry_run_and_preserves_existing(tmp_path):
    controller = workspace.Controller(ROOT)
    controller.sources["macWorkspaceRoot"] = str(tmp_path)
    existing = tmp_path / controller.registry.modules["cloud-functions"].mac_path
    existing.mkdir(parents=True)
    result = controller.materialize({"platform"}, dry_run=True)["result"]
    action = next(item for item in result["actions"] if item["module"] == "cloud-functions")
    assert action["action"] == "preserve-existing"
    assert result["dryRun"] is True


def test_repository_key_normalizes_ssh_and_https():
    assert workspace.repository_key("git@github.com:re8ch/example.git") == workspace.repository_key("https://github.com/re8ch/example.git")
