#!/usr/bin/env python3
"""Materialize one immutable Kubernetes ReleaseOperation for the SSA executor."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import uuid
from pathlib import Path

import yaml

from unified_bundle import canonical, digest, validate_schema


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource", type=Path, required=True)
    parser.add_argument("--static-release", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    resource = json.loads(args.resource.read_text())
    release = json.loads(args.static_release.read_text())
    contract = yaml.safe_load(args.contract.read_text())
    metadata = json.loads(args.metadata.read_text())
    spec = resource["spec"]
    release_id = spec["releaseRef"]
    if release_id != release["metadata"]["name"] or release_id != contract["metadata"]["name"]:
        raise SystemExit("release identity mismatch")
    contract_digest = digest(canonical(contract))
    if spec["contractDigest"] != contract_digest or release["spec"]["contractDigest"] != contract_digest:
        raise SystemExit("contract digest mismatch")
    state_path = args.state_dir / "state" / f"{release_id}.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else None
    projected_digest = release["spec"].get("bundleDigest")
    bundle_digest = spec["approvedDigest"]
    if not projected_digest:
        raise SystemExit("StaticRelease has no Git-projected bundle digest")
    if spec["action"] == "rollback":
        if not state or state["appliedDigest"] != projected_digest:
            raise SystemExit("rollback requires the accepted digest to match the Git projection")
        if not spec.get("approvalRef"):
            raise SystemExit("rollback requires an approval reference")
        history = next((item for item in state.get("history", []) if item["appliedDigest"] == bundle_digest), None)
        if not history:
            raise SystemExit("rollback digest is not present in accepted signed history")
        if metadata["payloadDigest"] != history["payloadDigest"] or metadata["inventoryDigest"] != history["inventoryDigest"]:
            raise SystemExit("rollback bundle metadata does not match accepted signed history")
    elif bundle_digest != projected_digest:
        raise SystemExit("operation does not approve the Git-projected bundle digest")
    now = dt.datetime.now(dt.timezone.utc)
    timeout = min(int(spec["timeoutSeconds"]), 3600)
    provenance = contract["spec"]["provenance"]
    operation = {
        "apiVersion": "delivery.re8ch.com/release-operation/v1alpha1",
        "operationId": str(uuid.UUID(resource["metadata"]["uid"])),
        "sequence": 1 if state is None else state["sequence"] + 1,
        "action": spec["action"], "reason": spec.get("reason", ""),
        "idempotencyKey": spec["idempotencyKey"], "releaseId": release_id,
        "target": f'{contract["spec"]["target"]["cluster"]}/{contract["spec"]["target"]["namespace"]}',
        "contractDigest": contract_digest,
        "bundle": {"reference": release["spec"]["bundleRepository"], "digest": bundle_digest,
                   "payloadDigest": metadata["payloadDigest"], "signer": provenance["signer"], "issuer": provenance["issuer"]},
        "inventoryDigest": metadata["inventoryDigest"],
        "github": {"repository": os.environ["GITHUB_REPOSITORY"], "workflow": provenance["workflow"],
                   "ref": os.environ["GITHUB_REF"], "commit": os.environ["GITHUB_SHA"], "runId": os.environ["GITHUB_RUN_ID"],
                   "environment": provenance["environment"], "audience": provenance["audience"]},
        "issuedAt": now.isoformat(), "expiresAt": (now + dt.timedelta(seconds=timeout)).isoformat(),
        "nonce": uuid.uuid4().hex,
    }
    if state:
        operation["previousApprovedDigest"] = state["appliedDigest"]
    if spec.get("approvalRef"):
        operation["approvalRef"] = spec["approvalRef"]
    if spec.get("forceConflictObjects"):
        operation["forceConflictObjects"] = spec["forceConflictObjects"]
    validate_schema(operation, "release-operation.schema.json")
    args.output.write_bytes(canonical(operation))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
