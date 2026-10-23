#!/usr/bin/env python3
"""Materialize typed release requests and durable ConfigMap state envelopes."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import secrets
import uuid
from pathlib import Path

import yaml

from unified_bundle import canonical, digest, validate_schema


def state_configmap_name(release_id: str) -> str:
    """Return a stable DNS label for a release's durable executor state."""
    prefix = "ssa-state-"
    candidate = prefix + release_id
    if len(candidate) <= 63:
        return candidate
    suffix = hashlib.sha256(release_id.encode()).hexdigest()[:10]
    head = release_id[: 63 - len(prefix) - len(suffix) - 1].rstrip("-")
    return f"{prefix}{head}-{suffix}"


def compact(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def hydrate(configmap: Path, state_dir: Path, release_id: str) -> None:
    data = json.loads(configmap.read_text()).get("data", {})
    (state_dir / "state").mkdir(parents=True, exist_ok=True)
    (state_dir / "results" / release_id).mkdir(parents=True, exist_ok=True)
    if "accepted-state.json" in data:
        state = json.loads(data["accepted-state.json"])
        validate_schema(state, "release-accepted-state.schema.json")
        (state_dir / "state" / f"{release_id}.json").write_text(compact(state))
    if "last-result.json" in data:
        result = json.loads(data["last-result.json"])
        validate_schema(result, "release-operation-result.schema.json")
        (state_dir / "results" / release_id / f'{result["operationId"]}.json').write_text(compact(result))


def request(contract_path: Path, metadata_path: Path, state_dir: Path, reference: str,
            oci_digest: str, output: Path, action: str = "reconcile", force_objects: list[str] | None = None) -> None:
    contract = yaml.safe_load(contract_path.read_text())
    validate_schema(contract, "release-contract.schema.json")
    metadata = json.loads(metadata_path.read_text())
    release_id = contract["metadata"]["name"]
    state_path = state_dir / "state" / f"{release_id}.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else None
    now = dt.datetime.now(dt.timezone.utc)
    provenance = contract["spec"]["provenance"]
    operation = {
        "apiVersion": "delivery.re8ch.com/release-operation/v1alpha1",
        "operationId": str(uuid.uuid4()), "sequence": 1 if state is None else state["sequence"] + 1,
        "action": action,
        "releaseId": release_id,
        "target": f'{contract["spec"]["target"]["cluster"]}/{contract["spec"]["target"]["namespace"]}',
        "contractDigest": digest(canonical(contract)),
        "bundle": {"reference": reference, "digest": oci_digest, "payloadDigest": metadata["payloadDigest"],
                   "signer": provenance["signer"], "issuer": provenance["issuer"]},
        "inventoryDigest": metadata["inventoryDigest"],
        "github": {"repository": os.environ["GITHUB_REPOSITORY"], "workflow": provenance["workflow"],
                   "ref": os.environ["GITHUB_REF"], "commit": os.environ["GITHUB_SHA"], "runId": os.environ["GITHUB_RUN_ID"],
                   "environment": provenance["environment"], "audience": provenance["audience"]},
        "issuedAt": now.isoformat(), "expiresAt": (now + dt.timedelta(minutes=10)).isoformat(),
        "nonce": secrets.token_hex(16),
        "idempotencyKey": secrets.token_hex(16),
    }
    if state:
        operation["previousApprovedDigest"] = state["appliedDigest"]
    if force_objects:
        operation["forceConflictObjects"] = sorted(force_objects)
    validate_schema(operation, "release-operation.schema.json")
    output.write_text(compact(operation))


def persist(state_dir: Path, release_id: str, result_path: Path, output: Path) -> None:
    state = json.loads((state_dir / "state" / f"{release_id}.json").read_text())
    result = json.loads(result_path.read_text())
    validate_schema(state, "release-accepted-state.schema.json")
    validate_schema(result, "release-operation-result.schema.json")
    output.write_text(compact({
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": state_configmap_name(release_id), "namespace": "rook-ceph"},
        "data": {"accepted-state.json": compact(state), "last-result.json": compact(result)},
    }))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser("hydrate")
    load.add_argument("--configmap", type=Path, required=True); load.add_argument("--state-dir", type=Path, required=True)
    load.add_argument("--release-id", required=True)
    make = sub.add_parser("request")
    make.add_argument("--contract", type=Path, required=True); make.add_argument("--metadata", type=Path, required=True)
    make.add_argument("--state-dir", type=Path, required=True); make.add_argument("--reference", required=True)
    make.add_argument("--digest", required=True); make.add_argument("--output", type=Path, required=True)
    make.add_argument("--action", choices=["reconcile", "force-reconcile", "reset", "suspend", "resume", "rollback"], default="reconcile")
    make.add_argument("--force-object", action="append", default=[])
    save = sub.add_parser("persist")
    save.add_argument("--state-dir", type=Path, required=True); save.add_argument("--release-id", required=True)
    save.add_argument("--result", type=Path, required=True); save.add_argument("--output", type=Path, required=True)
    state_name = sub.add_parser("state-name")
    state_name.add_argument("--release-id", required=True)
    args = parser.parse_args()
    if args.command == "hydrate": hydrate(args.configmap, args.state_dir, args.release_id)
    elif args.command == "request": request(args.contract, args.metadata, args.state_dir, args.reference, args.digest, args.output, args.action, args.force_object)
    elif args.command == "persist": persist(args.state_dir, args.release_id, args.result, args.output)
    else: print(state_configmap_name(args.release_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
