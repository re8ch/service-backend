#!/usr/bin/env python3
"""Promote catalog entries only when bound live evidence closes every gate."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "operations/plugin-capabilities/registry.json"
EVIDENCE = ROOT / "operations/plugin-capabilities/verified-capabilities.json"
REPORT = ROOT / "operations/plugin-capabilities/verified-capability-report.json"

COMMON_GATES = ("deployment", "invocation", "http", "authorization", "semantic", "ui")
MUTATION_GATES = ("failureRecovery", "cleanup")
STATUSES = {"not_run", "passed", "failed"}
READ_ONLY_DISCOVERY = {"mihomos.workloads.list", "mihomos.controllers.list"}
IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def definitions(path: Path = CATALOG) -> dict[str, dict]:
    value = json.loads(path.read_text())
    tools = value["servers"][0]["_meta"]["io.modelcontextprotocol.registry/publisher-provided"][
        "io.github.stacklok"]["streamable-http"]["tool_definitions"]
    indexed = {tool["name"]: tool for tool in tools}
    if len(indexed) != len(tools):
        raise ValueError("Catalog contains duplicate tool names")
    return indexed


def required_gates(tool: dict) -> tuple[str, ...]:
    read_only = tool.get("annotations", {}).get("readOnlyHint")
    # Registry metadata intentionally exposes only the public definition. Fall
    # back to the reviewed native method for older entries.
    if read_only is None:
        read_only = (tool.get("_meta", {}).get("re8ch", {}).get("method") == "GET"
                     or tool.get("name") in READ_ONLY_DISCOVERY)
    return COMMON_GATES if read_only else COMMON_GATES + MUTATION_GATES


def _validate_gate(name: str, gate: object) -> None:
    if not isinstance(gate, dict) or set(gate) != {"status", "evidenceRefs"}:
        raise ValueError(f"Gate {name} must contain only status and evidenceRefs")
    if gate["status"] not in STATUSES:
        raise ValueError(f"Gate {name} has an unsupported status")
    refs = gate["evidenceRefs"]
    if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref for ref in refs):
        raise ValueError(f"Gate {name} evidenceRefs must be non-empty strings")
    if gate["status"] != "not_run" and not refs:
        raise ValueError(f"Gate {name} needs evidence for {gate['status']}")


def build(catalog_path: Path = CATALOG, evidence_path: Path = EVIDENCE) -> dict:
    tools = definitions(catalog_path)
    evidence = json.loads(evidence_path.read_text())
    if evidence.get("version") != 1 or evidence.get("cluster") != "re8ch-k3s":
        raise ValueError("Unsupported verified capability evidence")
    records = evidence.get("records")
    if not isinstance(records, list):
        raise ValueError("Evidence records must be a list")
    indexed = {}
    for record in records:
        name = record.get("toolName") if isinstance(record, dict) else None
        if not name or name in indexed or name not in tools:
            raise ValueError("Evidence must identify one unique catalog tool")
        tool = tools[name]
        meta = tool.get("_meta", {}).get("re8ch", {})
        expected_input = hashlib.sha256(canonical(tool["inputSchema"])).hexdigest()
        if record.get("inputSchemaSha256") != expected_input:
            raise ValueError(f"Input contract evidence is stale for {name}")
        schema_hash = meta.get("schemaHash")
        if schema_hash is not None and record.get("schemaHash") != schema_hash:
            raise ValueError(f"Native schema evidence is stale for {name}")
        if schema_hash is not None and not SHA256.fullmatch(schema_hash):
            raise ValueError(f"Catalog schema hash is invalid for {name}")
        if not IMAGE.fullmatch(record.get("nativeImage", "")):
            raise ValueError(f"Evidence must bind {name} to an immutable image digest")
        if not isinstance(record.get("recordedAt"), str) or not record["recordedAt"]:
            raise ValueError(f"Evidence needs a timestamp for {name}")
        gates = record.get("gates")
        required = required_gates(tool)
        if not isinstance(gates, dict) or set(gates) != set(required):
            raise ValueError(f"Evidence gates do not match {name}: expected {required}")
        for gate_name, gate in gates.items():
            _validate_gate(gate_name, gate)
        indexed[name] = record

    capabilities = []
    for name, tool in sorted(tools.items()):
        required = required_gates(tool)
        record = indexed.get(name)
        if record is None:
            state, blockers = "inventory_only", list(required)
        else:
            blockers = [gate for gate in required if record["gates"][gate]["status"] != "passed"]
            state = "verified" if not blockers else "evidence_incomplete"
        capabilities.append({
            "toolName": name,
            "kind": "read" if required == COMMON_GATES else "mutation",
            "state": state,
            "blockingGates": blockers,
            **({"recordedAt": record["recordedAt"], "nativeImage": record["nativeImage"]} if record else {}),
        })
    counts = Counter(row["state"] for row in capabilities)
    kinds = Counter(row["kind"] for row in capabilities)
    return {
        "version": 1,
        "cluster": "re8ch-k3s",
        "meaning": "verified requires bound deployment, invocation, HTTP, authorization, semantic and UI evidence; mutations also require failure recovery and cleanup",
        "summary": {
            "catalogTools": len(capabilities),
            "readTools": kinds["read"],
            "mutationTools": kinds["mutation"],
            "verified": counts["verified"],
            "evidenceIncomplete": counts["evidence_incomplete"],
            "inventoryOnly": counts["inventory_only"],
        },
        "capabilities": capabilities,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the deterministic report")
    args = parser.parse_args()
    rendered = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if args.write:
        REPORT.write_text(rendered)
        return 0
    if not REPORT.exists() or REPORT.read_text() != rendered:
        print("verified-capability-report.json is stale; run verified_capabilities.py --write")
        return 1
    print("Verified capability report is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
