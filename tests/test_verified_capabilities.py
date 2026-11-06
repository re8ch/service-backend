import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "operations/plugin-capabilities/verified_capabilities.py"
spec = importlib.util.spec_from_file_location("verified_capabilities", SCRIPT)
verified = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verified)


def write_catalog(path, *, method="GET"):
    tool = {
        "name": "example.tool",
        "inputSchema": {"type": "object", "additionalProperties": False},
        "_meta": {"re8ch": {"method": method, "schemaHash": "a" * 64}},
    }
    value = {"servers": [{"_meta": {"io.modelcontextprotocol.registry/publisher-provided": {
        "io.github.stacklok": {"streamable-http": {"tool_definitions": [tool]}}
    }}}]}
    path.write_text(json.dumps(value))
    return tool


def record(tool, *, method="GET"):
    gates = verified.COMMON_GATES + (() if method == "GET" else verified.MUTATION_GATES)
    return {
        "toolName": tool["name"],
        "inputSchemaSha256": __import__("hashlib").sha256(verified.canonical(tool["inputSchema"])).hexdigest(),
        "schemaHash": "a" * 64,
        "nativeImage": "registry.example.test/cloud@sha256:" + "b" * 64,
        "recordedAt": "2026-11-07T00:00:00Z",
        "gates": {name: {"status": "passed", "evidenceRefs": [f"audit://test/{name}"]} for name in gates},
    }


def test_current_catalog_makes_no_unsubstantiated_verified_claims():
    report = verified.build()
    assert report["summary"] == {
        "catalogTools": 373,
        "readTools": 297,
        "mutationTools": 76,
        "verified": 0,
        "evidenceIncomplete": 0,
        "inventoryOnly": 373,
    }


def test_complete_bound_read_evidence_is_verified(tmp_path):
    catalog = tmp_path / "catalog.json"
    evidence = tmp_path / "evidence.json"
    tool = write_catalog(catalog)
    evidence.write_text(json.dumps({"version": 1, "cluster": "re8ch-k3s", "records": [record(tool)]}))
    report = verified.build(catalog, evidence)
    assert report["summary"]["verified"] == 1
    assert report["capabilities"][0]["blockingGates"] == []


def test_mutation_cannot_skip_cleanup_or_failure_recovery(tmp_path):
    catalog = tmp_path / "catalog.json"
    evidence = tmp_path / "evidence.json"
    tool = write_catalog(catalog, method="POST")
    value = record(tool, method="POST")
    del value["gates"]["cleanup"]
    evidence.write_text(json.dumps({"version": 1, "cluster": "re8ch-k3s", "records": [value]}))
    with pytest.raises(ValueError, match="Evidence gates do not match"):
        verified.build(catalog, evidence)


def test_contract_or_image_drift_rejects_evidence(tmp_path):
    catalog = tmp_path / "catalog.json"
    evidence = tmp_path / "evidence.json"
    tool = write_catalog(catalog)
    value = record(tool)
    value["inputSchemaSha256"] = "0" * 64
    evidence.write_text(json.dumps({"version": 1, "cluster": "re8ch-k3s", "records": [value]}))
    with pytest.raises(ValueError, match="Input contract evidence is stale"):
        verified.build(catalog, evidence)


def test_checked_in_report_is_deterministic():
    assert verified.REPORT.read_text() == json.dumps(
        verified.build(), indent=2, ensure_ascii=False
    ) + "\n"
