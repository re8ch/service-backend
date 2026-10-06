#!/usr/bin/env python3
"""Reconcile the handoff backlog with the current generated operation inventory.

This is an evidence ledger, not an implementation or deployment claim.  The
native tuple is the identity because some source routes have no operationId.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HANDOFF = Path(__file__).resolve().parent
BASELINE = HANDOFF / "unimplemented-operations-20261004.csv"
OUTPUT = HANDOFF / "operation-progress.json"
INVENTORY = ROOT / "operations/plugin-capabilities/registry.json"
MATRIX = ROOT / "operations/plugin-capabilities/native-operation-matrix.json"
DEPLOYMENT = HANDOFF / "deployment-evidence-20261006.json"
CANARY = HANDOFF / "network-canary-acceptance-20261006.json"
TOOLS = ROOT / "operations/cloud-platform/plugin-operation-tools.json"


def key(row: dict) -> tuple[str, str, str]:
    return row["groupVersion"], row["method"].upper(), row["path"]


def build() -> dict:
    with BASELINE.open(newline="") as stream:
        baseline = list(csv.DictReader(stream))
    inventory = json.loads(INVENTORY.read_text())["servers"][0]["_meta"][
        "io.modelcontextprotocol.registry/publisher-provided"]["io.github.stacklok"][
        "streamable-http"]["re8ch_capabilities"]["operations"]
    sources = json.loads(MATRIX.read_text())["sources"]
    deployment = json.loads(DEPLOYMENT.read_text())
    if deployment["cluster"] != "re8ch-k3s" or not deployment["nativeImage"].startswith("registry.re8ch.com/functions-shared/re8ch-cloud-platform@sha256:"):
        raise ValueError("Invalid network deployment evidence")
    definitions = {item["name"]: item for item in json.loads(TOOLS.read_text())["tools"]}
    deployed = set()
    for item in deployment["tools"]:
        tool = definitions.get(item["name"])
        digest = hashlib.sha256(json.dumps(tool["inputSchema"], sort_keys=True, separators=(",", ":")).encode()).hexdigest() if tool else None
        if digest != item["inputSchemaSha256"] or item["name"] in deployed:
            raise ValueError("Deployment evidence does not match a unique current tool contract")
        deployed.add(item["name"])
    canary = json.loads(CANARY.read_text())
    samples = {key(item) for item in canary["operations"]}
    source_hashes = {}
    for source in sources:
        schema = ROOT / "operations/plugin-capabilities" / source["schemaFile"]
        document = json.loads(schema.read_text())
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        source_hashes[source["groupVersion"]] = hashlib.sha256(canonical).hexdigest()
    indexed = {}
    for row in inventory:
        identity = key(row)
        if identity in indexed:
            raise ValueError(f"duplicate inventory operation: {identity}")
        indexed[identity] = row
    seen = set()
    operations = []
    for row in baseline:
        identity = key(row)
        if identity in seen:
            raise ValueError(f"duplicate baseline operation: {identity}")
        seen.add(identity)
        live = indexed.get(identity)
        tool = live.get("tool") if live else None
        observed_deployed = tool in deployed
        schema_hash = source_hashes.get(row["groupVersion"])
        operations.append({
            "plugin": row["plugin"], "groupVersion": identity[0],
            "method": identity[1], "path": identity[2],
            "operationId": row["operationId"] or None,
            "disposition": row["disposition"],
            "schemaHash": schema_hash,
            "authorization": "platform_admin_required",
            "tool": tool,
            "registration": "deployed" if observed_deployed else live.get("registration", "absent_from_current_inventory") if live else "absent_from_current_inventory",
            "deploymentDigest": deployment["nativeImage"].split("@", 1)[1] if observed_deployed else None,
            "httpEvidence": None, "semanticEvidence": None,
            "cleanupEvidence": None,
            "sampleEvidence": str(CANARY.relative_to(ROOT)) if identity in samples else None,
        })
    if len(operations) != 683:
        raise ValueError(f"expected 683 baseline operations, got {len(operations)}")
    return {
        "version": 1,
        "cluster": "re8ch-k3s",
        "baseline": str(BASELINE.relative_to(ROOT)),
        "baselineSha256": hashlib.sha256(BASELINE.read_bytes()).hexdigest(),
        "meaning": "Unverified progress ledger; null evidence is not a passing result",
        "operations": operations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the deterministic ledger")
    args = parser.parse_args()
    rendered = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if args.write:
        OUTPUT.write_text(rendered)
        return 0
    if not OUTPUT.exists() or OUTPUT.read_text() != rendered:
        print("operation-progress.json is stale; run progress.py --write")
        return 1
    print("683 baseline operations reconciled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
