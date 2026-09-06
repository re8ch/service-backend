#!/usr/bin/env python3
"""Pure contracts for autonomous tenant compute and volume claims."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

API_VERSION = "tenancy.re8ch.com/v1alpha1"
TTL_MIN_SECONDS = 300
TTL_MAX_SECONDS = 2_592_000
STORAGE_CLASSES = {"rbd": "rbd-r640", "cephfs": "cephfs-re8ch"}
NAME_RE = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
DIGEST_RE = re.compile(r"^registry\.re8ch\.com/([a-z0-9][a-z0-9._-]*)/.+@sha256:[a-f0-9]{64}$")
QUANTITY_RE = re.compile(r"^[1-9][0-9]*(?:m|Ki|Mi|Gi|Ti)?$")
FORBIDDEN_PARAMETER_FRAGMENTS = (
    "password", "token", "secret", "kubeconfig", "privatekey", "private_key",
    "credential", "serviceaccount", "hostpath", "privileged", "hostnetwork",
    "hostpid", "hostipc", "nodeport", "manifest", "gpu",
)

CAPABILITIES: dict[str, dict[str, Any]] = {
    "compute.workload.v1": {
        "kinds": ["Job", "Deployment"],
        "parameters": ["kind", "imageDigest", "command", "args", "env", "resources", "replicas", "parallelism", "completions", "volumeMounts", "exposure", "egressClass"],
        "binding": "workloadRef",
        "mutable": ["replicas", "imageDigest", "command", "args", "env", "resources", "volumeMounts", "exposure"],
    },
    "storage.volume.v1": {
        "parameters": ["sizeGiB", "accessMode", "plan", "retentionPolicy"],
        "binding": "volumeRef",
        "mutable": ["sizeGiB"],
    },
}


class ContractError(ValueError):
    pass


def capabilities() -> list[dict[str, Any]]:
    return [{"name": name, **copy.deepcopy(contract), "ttlSeconds": {"minimum": TTL_MIN_SECONDS, "maximum": TTL_MAX_SECONDS, "renewable": True}, "requiresApproval": False} for name, contract in sorted(CAPABILITIES.items())]


def _name(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) > 63 or not NAME_RE.fullmatch(value):
        raise ContractError(f"{field} must be a DNS label")
    return value


def _positive_int(value: Any, field: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ContractError(f"{field} must be an integer between 1 and {maximum}")
    return value


def _quantity(value: Any, field: str) -> str:
    if not isinstance(value, str) or not QUANTITY_RE.fullmatch(value):
        raise ContractError(f"{field} must be a positive Kubernetes resource quantity")
    return value


def _resource_quantity(value: Any, field: str) -> str:
    value = _quantity(value, field)
    if field != "resources.cpu" and not value.endswith(("Ki", "Mi", "Gi", "Ti")):
        raise ContractError(f"{field} must use Ki, Mi, Gi or Ti")
    if field == "resources.cpu" and value.endswith(("Ki", "Mi", "Gi", "Ti")):
        raise ContractError("resources.cpu must use cores or millicores")
    return value


def _normalise_compute(parameters: dict[str, Any], harbor_project: str) -> dict[str, Any]:
    kind = parameters.get("kind")
    if kind not in {"Job", "Deployment"}:
        raise ContractError("compute kind must be Job or Deployment")
    image = parameters.get("imageDigest")
    match = DIGEST_RE.fullmatch(image or "")
    if not match:
        raise ContractError("imageDigest must be a digest-pinned registry.re8ch.com image")
    if match.group(1) != harbor_project:
        raise ContractError("imageDigest must be in the authenticated tenant Harbor project")
    resources = parameters.get("resources")
    if not isinstance(resources, dict) or set(resources) != {"cpu", "memory", "ephemeralStorage"}:
        raise ContractError("resources must contain cpu, memory and ephemeralStorage")
    resources = {"cpu": _resource_quantity(resources["cpu"], "resources.cpu"), "memory": _resource_quantity(resources["memory"], "resources.memory"), "ephemeralStorage": _resource_quantity(resources["ephemeralStorage"], "resources.ephemeralStorage")}
    command = parameters.get("command", [])
    args = parameters.get("args", [])
    if not isinstance(command, list) or not isinstance(args, list) or not all(isinstance(v, str) for v in command + args):
        raise ContractError("command and args must be string arrays")
    env = parameters.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise ContractError("env must contain string values; secret references are not accepted")
    mounts = parameters.get("volumeMounts", [])
    if not isinstance(mounts, list):
        raise ContractError("volumeMounts must be an array")
    clean_mounts = []
    for mount in mounts:
        if not isinstance(mount, dict) or set(mount) - {"claimName", "mountPath", "readOnly"}:
            raise ContractError("volumeMounts only accepts claimName, mountPath and readOnly")
        claim_name = _name(mount.get("claimName"), "volumeMounts.claimName")
        path = mount.get("mountPath")
        if not isinstance(path, str) or not path.startswith("/") or path == "/":
            raise ContractError("volumeMounts.mountPath must be an absolute non-root path")
        clean_mounts.append({"claimName": claim_name, "mountPath": path, "readOnly": bool(mount.get("readOnly", False))})
    exposure = parameters.get("exposure", {"enabled": False})
    if not isinstance(exposure, dict) or set(exposure) - {"enabled", "port", "healthPath"}:
        raise ContractError("exposure only accepts enabled, port and healthPath")
    clean_exposure: dict[str, Any] = {"enabled": bool(exposure.get("enabled", False))}
    if clean_exposure["enabled"]:
        if kind != "Deployment":
            raise ContractError("public exposure is only available for Deployment")
        clean_exposure["port"] = _positive_int(exposure.get("port"), "exposure.port", 65535)
        health_path = exposure.get("healthPath", "/")
        if not isinstance(health_path, str) or not health_path.startswith("/"):
            raise ContractError("exposure.healthPath must begin with /")
        clean_exposure["healthPath"] = health_path
    result: dict[str, Any] = {"kind": kind, "imageDigest": image, "command": command, "args": args, "env": dict(sorted(env.items())), "resources": resources, "volumeMounts": clean_mounts, "exposure": clean_exposure, "egressClass": parameters.get("egressClass", "metered-public")}
    if result["egressClass"] != "metered-public":
        raise ContractError("egressClass must be metered-public")
    if kind == "Deployment":
        result["replicas"] = _positive_int(parameters.get("replicas", 1), "replicas", 20)
        if "parallelism" in parameters or "completions" in parameters:
            raise ContractError("parallelism and completions are Job-only")
    else:
        result["parallelism"] = _positive_int(parameters.get("parallelism", 1), "parallelism", 20)
        result["completions"] = _positive_int(parameters.get("completions", 1), "completions", 1000)
        if "replicas" in parameters:
            raise ContractError("replicas is Deployment-only")
    return result


def _normalise_volume(parameters: dict[str, Any]) -> dict[str, Any]:
    size = _positive_int(parameters.get("sizeGiB"), "sizeGiB", 1024)
    plan_name = parameters.get("plan", "rbd")
    if plan_name not in STORAGE_CLASSES:
        raise ContractError("volume plan must be rbd or cephfs")
    access_mode = parameters.get("accessMode", "ReadWriteOnce" if plan_name == "rbd" else "ReadWriteMany")
    allowed_mode = "ReadWriteOnce" if plan_name == "rbd" else "ReadWriteMany"
    if access_mode != allowed_mode:
        raise ContractError(f"{plan_name} requires {allowed_mode}")
    if parameters.get("retentionPolicy", "Retain") != "Retain":
        raise ContractError("independent volumes must use Retain")
    return {"sizeGiB": size, "plan": plan_name, "storageClass": STORAGE_CLASSES[plan_name], "accessMode": access_mode, "retentionPolicy": "Retain"}


def normalize_claim(claim: dict[str, Any], *, authenticated_tenant: str, harbor_project: str | None = None) -> dict[str, Any]:
    if not isinstance(claim, dict):
        raise ContractError("claim must be an object")
    metadata, spec = claim.get("metadata", {}), claim.get("spec", {})
    if not isinstance(metadata, dict) or not isinstance(spec, dict):
        raise ContractError("metadata and spec must be objects")
    tenant = spec.get("tenantRef", metadata.get("tenantRef"))
    if tenant != authenticated_tenant:
        raise ContractError("tenantRef must match the authenticated tenant")
    tenant = _name(tenant, "tenantRef")
    name = _name(metadata.get("name"), "metadata.name")
    idempotency_key = spec.get("idempotencyKey", metadata.get("idempotencyKey"))
    if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 128:
        raise ContractError("idempotencyKey must contain 8 to 128 characters")
    ttl = spec.get("ttlSeconds")
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not TTL_MIN_SECONDS <= ttl <= TTL_MAX_SECONDS:
        raise ContractError(f"ttlSeconds must be between {TTL_MIN_SECONDS} and {TTL_MAX_SECONDS}")
    capability = spec.get("capability")
    if capability not in CAPABILITIES:
        raise ContractError("capability is not published")
    parameters = spec.get("parameters", {})
    if not isinstance(parameters, dict):
        raise ContractError("parameters must be an object")
    forbidden = [key for key in parameters if any(fragment in key.lower() for fragment in FORBIDDEN_PARAMETER_FRAGMENTS)]
    if forbidden:
        raise ContractError("credential, host, privileged, manifest and GPU parameters are forbidden")
    unknown = sorted(set(parameters) - set(CAPABILITIES[capability]["parameters"]))
    if unknown:
        raise ContractError(f"parameters are not allowed for {capability}: {', '.join(unknown)}")
    normalized_parameters = _normalise_compute(parameters, harbor_project or tenant) if capability == "compute.workload.v1" else _normalise_volume(parameters)
    return {"apiVersion": API_VERSION, "kind": "TenantResourceClaim", "metadata": {"name": name}, "spec": {"tenantRef": tenant, "idempotencyKey": idempotency_key, "capability": capability, "ttlSeconds": ttl, "parameters": normalized_parameters}}


def _quota_impact(normalized: dict[str, Any]) -> dict[str, Any]:
    parameters = normalized["spec"]["parameters"]
    if normalized["spec"]["capability"] == "storage.volume.v1":
        return {"claims": 1, "storageGiB": parameters["sizeGiB"]}
    units = parameters.get("replicas", parameters.get("parallelism", 1))
    return {"claims": 1, "workloads": 1, "pods": units, "cpuPerPod": parameters["resources"]["cpu"], "memoryPerPod": parameters["resources"]["memory"], "ephemeralStoragePerPod": parameters["resources"]["ephemeralStorage"], "publicEndpoints": 1 if parameters["exposure"]["enabled"] else 0}


def plan(claim: dict[str, Any], *, authenticated_tenant: str, harbor_project: str | None = None) -> dict[str, Any]:
    normalized = normalize_claim(claim, authenticated_tenant=authenticated_tenant, harbor_project=harbor_project)
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    return {"decision": "preview", "tenantRef": authenticated_tenant, "capability": normalized["spec"]["capability"], "normalizedClaim": normalized, "claimHash": f"sha256:{hashlib.sha256(canonical).hexdigest()}", "quotaImpact": _quota_impact(normalized), "requiresApproval": False, "bindingType": CAPABILITIES[normalized["spec"]["capability"]]["binding"], "credentialMaterialReturned": False}


def validate_update(current: dict[str, Any], proposed: dict[str, Any], *, authenticated_tenant: str, harbor_project: str | None = None) -> dict[str, Any]:
    old = normalize_claim(current, authenticated_tenant=authenticated_tenant, harbor_project=harbor_project)
    new = normalize_claim(proposed, authenticated_tenant=authenticated_tenant, harbor_project=harbor_project)
    for field in ("tenantRef", "idempotencyKey", "capability"):
        if old["spec"][field] != new["spec"][field]:
            raise ContractError(f"{field} is immutable")
    if old["metadata"]["name"] != new["metadata"]["name"]:
        raise ContractError("metadata.name is immutable")
    if old["spec"]["capability"] == "compute.workload.v1":
        if old["spec"]["parameters"]["kind"] == "Job" and old["spec"]["parameters"] != new["spec"]["parameters"]:
            raise ContractError("Job parameters are immutable; create a new claim")
        if old["spec"]["parameters"]["kind"] != new["spec"]["parameters"]["kind"]:
            raise ContractError("compute kind is immutable")
    else:
        if {key: value for key, value in old["spec"]["parameters"].items() if key != "sizeGiB"} != {key: value for key, value in new["spec"]["parameters"].items() if key != "sizeGiB"}:
            raise ContractError("volume plan, access mode and retention policy are immutable")
        if new["spec"]["parameters"]["sizeGiB"] < old["spec"]["parameters"]["sizeGiB"]:
            raise ContractError("volume size cannot be reduced")
    return new
