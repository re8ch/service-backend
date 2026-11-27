import json
import importlib.util
from pathlib import Path

MODULE = Path(__file__).parents[1] / "operations/github-transaction-context/inventory.py"
SPEC = importlib.util.spec_from_file_location("transaction_context_inventory", MODULE)
assert SPEC and SPEC.loader
inventory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory)


def valid_inventory():
    return json.loads(inventory.INVENTORY.read_text())


def test_checked_in_inventory_matches_catalog():
    assert inventory.validate(valid_inventory()) == []


def test_duplicate_repository_is_rejected():
    data = valid_inventory()
    data["repositories"].append(data["repositories"][0])
    assert "repository identities must be unique" in inventory.validate(data)


def test_unknown_state_is_rejected():
    data = valid_inventory()
    data["repositories"][0]["enforcement"] = "maybe"
    assert any("unsupported enforcement state" in error for error in inventory.validate(data))


def test_missing_capability_key_is_rejected():
    data = valid_inventory()
    del data["repositories"][0]["capabilities"]["projectContext"]
    assert any("capability keys must be complete" in error for error in inventory.validate(data))


def test_inventory_declares_single_developer_entry_point():
    data = valid_inventory()
    assert data["developerWorkflow"]["entryPoint"] == "create or select one Issue"
    assert data["developerWorkflow"]["inventoryAudience"].endswith("not a developer checklist")
