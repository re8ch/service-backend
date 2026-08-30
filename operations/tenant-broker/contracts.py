#!/usr/bin/env python3
"""Pure policy and response contracts for the RE8CH AI tenant broker."""

from __future__ import annotations

import hashlib
import json
from typing import Any

CAPABILITIES: dict[str, dict[str, Any]] = {
    "image.run.v1": {"parameters": ["imageDigest", "command", "args", "cpu", "memory", "gpu", "workspaceGiB", "ports", "timeoutSeconds", "restartPolicy"], "binding": "containerRunRef"},
    "database.run.v1": {"parameters": ["engine", "imageDigest", "cpu", "memory", "storageGiB", "backupClass", "retentionClass", "extensions"], "binding": "databaseRef"},
    "network.service-binding.v1": {"parameters": ["serviceRef", "ports", "protocol"], "binding": "originRef"},
    "storage.volume.v1": {"parameters": ["sizeGiB", "accessMode", "storageClass"], "binding": "volumeRef"},
    "storage.bucket.v1": {"parameters": ["quotaGiB", "region"], "binding": "credentialRef"},
}

FORBIDDEN_PARAMETER_FRAGMENTS = ("password", "token", "secret", "kubeconfig", "privatekey", "private_key", "credential")


class ContractError(ValueError):
    pass


def capabilities() -> list[dict[str, Any]]:
    return [{"name": name, **contract} for name, contract in sorted(CAPABILITIES.items())]


def plan(claim: dict[str, Any], *, authenticated_tenant: str) -> dict[str, Any]:
    tenant = claim.get("metadata", {}).get("tenantRef")
    if tenant != authenticated_tenant:
        raise ContractError("tenantRef must match the authenticated tenant")
    capability = claim.get("spec", {}).get("capability")
    if capability not in CAPABILITIES:
        raise ContractError("capability is not published")
    parameters = claim["spec"].get("parameters", {})
    forbidden = [key for key in parameters if any(fragment in key.lower() for fragment in FORBIDDEN_PARAMETER_FRAGMENTS)]
    if forbidden:
        raise ContractError("credential material is forbidden; the broker returns opaque references")
    allowed = set(CAPABILITIES[capability]["parameters"])
    unknown = sorted(set(parameters) - allowed)
    if unknown:
        raise ContractError(f"parameters are not allowed for {capability}: {', '.join(unknown)}")
    canonical = json.dumps(claim, sort_keys=True, separators=(",", ":")).encode()
    return {
        "decision": "preview",
        "tenantRef": tenant,
        "capability": capability,
        "claimHash": f"sha256:{hashlib.sha256(canonical).hexdigest()}",
        "requiresApproval": capability in {"database.run.v1", "network.service-binding.v1"},
        "bindingType": CAPABILITIES[capability]["binding"],
        "credentialMaterialReturned": False,
    }
