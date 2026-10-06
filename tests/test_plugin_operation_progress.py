import importlib.util
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "operations/plugin-capabilities/handoff/progress.py"
spec = importlib.util.spec_from_file_location("plugin_progress", SCRIPT)
progress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(progress)


def test_backlog_is_keyed_and_all_evidence_starts_unverified():
    ledger = progress.build()
    rows = ledger["operations"]
    assert len(rows) == 683
    assert len({progress.key(row) for row in rows}) == len(rows)
    assert Counter(row["plugin"] for row in rows)["anatomy"] == 49
    assert all(row["authorization"] == "platform_admin_required" for row in rows)
    assert all(row["httpEvidence"] is None and row["semanticEvidence"] is None for row in rows)
    assert all(row["tool"] for row in rows if row["registration"] != "not_implemented")
    assert all(row["schemaHash"] for row in rows if row["groupVersion"].startswith("apis/"))
    assert Counter(row["registration"] for row in rows) == {"deployed": 161, "not_implemented": 522}
    assert all(row["deploymentDigest"] for row in rows if row["registration"] == "deployed")
    assert sum(row["sampleEvidence"] is not None for row in rows) == 2


def test_ledger_matches_generated_source():
    assert progress.OUTPUT.read_text() == __import__("json").dumps(
        progress.build(), indent=2, ensure_ascii=False
    ) + "\n"
