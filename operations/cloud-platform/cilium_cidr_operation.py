"""Bounded administrator lifecycle for an unreferenced CiliumCIDRGroup canary."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from runtime import OperationError

GROUP = "apis/cilium.io/v2"
COLLECTION = "/apis/cilium.io/v2/ciliumcidrgroups"
NAME = re.compile(r"^tool-cidr-canary-[a-z0-9]{8,24}$")
CIDRS = ["198.51.100.0/24"]  # RFC 5737 documentation range; no live policy references it.
MARKER = "operations.re8ch.com/idempotency-hash"
LABEL = "operations.re8ch.com/managed"
SCHEMA_FILE = Path(__file__).with_name("cilium-native-openapi.json")
if not SCHEMA_FILE.exists():
    SCHEMA_FILE = Path(__file__).resolve().parents[1] / "plugin-capabilities/openapi/apis_cilium.io_v2.json"
DOCUMENT = json.loads(SCHEMA_FILE.read_text())
SCHEMA_HASH = hashlib.sha256(json.dumps(DOCUMENT, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _tool(name: str, operation_id: str, method: str, path: str, destructive: bool) -> dict:
    return {
        "name": name, "plugin": "cilium", "groupVersion": GROUP,
        "operationId": operation_id, "schemaHash": SCHEMA_HASH,
        "description": f"Plan, {method.lower()}, or inspect one tool-owned CiliumCIDRGroup canary. Fixed documentation CIDR; administrator only.",
        "inputSchema": {"type": "object", "additionalProperties": False,
                        "properties": {"phase": {"type": "string", "enum": ["plan", "execute", "status"]},
                                       "name": {"type": "string", "pattern": NAME.pattern},
                                       "idempotencyKey": {"type": "string", "minLength": 8, "maxLength": 128},
                                       "planHash": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
                        "required": ["phase", "name", "idempotencyKey"]},
        "annotations": {"readOnlyHint": False, "destructiveHint": destructive, "idempotentHint": True},
        "_meta": {"re8ch": {"plugin": "cilium", "groupVersion": GROUP, "operationId": operation_id,
                            "method": method, "path": path, "schemaHash": SCHEMA_HASH,
                            "httpConformance": "not_run", "semanticConformance": "not_run"}},
    }


TOOL = _tool("cilium.cidrgroup.create", "createCiliumIoV2CiliumCIDRGroup", "POST", COLLECTION, False)
DELETE_TOOL = _tool("cilium.cidrgroup.delete", "deleteCiliumIoV2CiliumCIDRGroup", "DELETE", COLLECTION + "/{name}", True)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _check_args(tool, args):
    from jsonschema import Draft202012Validator
    if list(Draft202012Validator(tool["inputSchema"]).iter_errors(args)) or not NAME.fullmatch(args.get("name", "")):
        raise OperationError("INVALID_ARGUMENT", "Use a named CiliumCIDRGroup canary and exact tool schema")


def _marker(args):
    return hashlib.sha256(args["idempotencyKey"].encode()).hexdigest()


def _path(args):
    return COLLECTION + "/" + args["name"]


def _body(args):
    return {"apiVersion": "cilium.io/v2", "kind": "CiliumCIDRGroup",
            "metadata": {"name": args["name"], "labels": {LABEL: args["name"]},
                         "annotations": {MARKER: _marker(args)}},
            "spec": {"externalCIDRs": CIDRS}}


def _read(service, path):
    response = service._plugin_http_get(path)
    if response["status"] == 404:
        return None
    if response["status"] != 200:
        raise OperationError("PLUGIN_READ_FAILED", "Cilium canary preflight failed")
    try:
        return json.loads(response["body"])
    except (ValueError, TypeError) as exc:
        raise OperationError("PLUGIN_READ_FAILED", "Cilium canary preflight was not JSON") from exc


def _owned(existing, args):
    metadata = existing.get("metadata", {})
    return (metadata.get("name") == args["name"]
            and metadata.get("annotations", {}).get(MARKER) == _marker(args)
            and metadata.get("labels", {}).get(LABEL) == args["name"]
            and existing.get("spec") == {"externalCIDRs": CIDRS})


def _view(existing):
    metadata = existing.get("metadata", {})
    return {"name": metadata.get("name"), "uid": metadata.get("uid"),
            "resourceVersion": metadata.get("resourceVersion"),
            "deleting": bool(metadata.get("deletionTimestamp")), "externalCIDRs": CIDRS}


def _live_schema(service):
    from plugin_capabilities import openapi
    if openapi(service, GROUP)["sha256"] != SCHEMA_HASH:
        raise OperationError("PLUGIN_SCHEMA_CHANGED", "Refresh the reviewed Cilium v2 contract")


def _acknowledge(response, operation_id, expected_statuses):
    from plugin_capabilities import operation_contract, validate_response_body
    if response["status"] not in expected_statuses:
        raise OperationError("PLUGIN_RESULT_UNKNOWN", f"Native Cilium write returned HTTP {response['status']}; query status")
    if not any(value.split(";", 1)[0].strip().lower() == "application/json"
               for value in response.get("headers", {}).get("content-type", [])):
        raise OperationError("PLUGIN_RESULT_UNKNOWN", "Cilium write response was not JSON; query status")
    try:
        body = json.loads(response["body"])
    except (ValueError, TypeError) as exc:
        raise OperationError("PLUGIN_RESULT_UNKNOWN", "Cilium write response was invalid; query status") from exc
    contract = operation_contract(DOCUMENT, operation_id)
    try:
        validate_response_body(DOCUMENT, contract, body, False)
    except OperationError as exc:
        raise OperationError("PLUGIN_RESULT_UNKNOWN", "Cilium write response violated its contract; query status") from exc
    return body


def create(service, args):
    _check_args(TOOL, args)
    body = _body(args)
    path = _path(args)
    existing = _read(service, path)
    if args["phase"] == "status":
        if existing is None:
            return {"state": "not_found", "schemaHash": SCHEMA_HASH}
        if not _owned(existing, args):
            raise OperationError("PLUGIN_OPERATION_CONFLICT", "CiliumCIDRGroup belongs to another request")
        return {"state": "observed", "schemaHash": SCHEMA_HASH, "result": _view(existing)}
    _live_schema(service)
    digest = hashlib.sha256(canonical({"schemaHash": SCHEMA_HASH, "method": "POST", "path": COLLECTION, "body": body})).hexdigest()
    if existing is not None:
        if _owned(existing, args):
            return {"state": "already_created", "planHash": digest, "result": _view(existing)}
        raise OperationError("PLUGIN_OPERATION_CONFLICT", "CiliumCIDRGroup name is already in use")
    if args["phase"] == "plan":
        return {"state": "ready", "planHash": digest, "schemaHash": SCHEMA_HASH,
                "target": {"name": args["name"], "externalCIDRs": CIDRS}}
    if args.get("planHash") != digest:
        raise OperationError("PLUGIN_PRECONDITION_FAILED", "Cilium canary plan changed; plan again")
    try:
        response = service._plugin_http_request("POST", COLLECTION, canonical(body))
    except OperationError:
        recovered = _read(service, path)
        if recovered and _owned(recovered, args):
            return {"state": "recovered", "planHash": digest, "result": _view(recovered)}
        raise OperationError("PLUGIN_RESULT_UNKNOWN", "Create response was lost; query status before retrying")
    acknowledged = _acknowledge(response, TOOL["operationId"], {200, 201, 202})
    if acknowledged.get("metadata", {}).get("name") != args["name"]:
        raise OperationError("PLUGIN_RESULT_UNKNOWN", "Create response target changed; query status")
    observed = _read(service, path)
    if not observed or not _owned(observed, args):
        raise OperationError("PLUGIN_RESULT_UNKNOWN", "Create was not confirmed; query status")
    return {"state": "created", "httpStatus": response["status"], "planHash": digest, "result": _view(observed)}


def delete(service, args):
    _check_args(DELETE_TOOL, args)
    path = _path(args)
    existing = _read(service, path)
    if existing is None:
        return {"state": "absent", "schemaHash": SCHEMA_HASH}
    if not _owned(existing, args):
        raise OperationError("PLUGIN_OPERATION_CONFLICT", "CiliumCIDRGroup is not owned by this request")
    metadata = existing["metadata"]
    uid, resource_version = metadata.get("uid"), metadata.get("resourceVersion")
    if not uid or not resource_version:
        raise OperationError("PLUGIN_PRECONDITION_FAILED", "Cilium canary identity is incomplete")
    if args["phase"] == "status":
        return {"state": "deleting" if metadata.get("deletionTimestamp") else "present",
                "schemaHash": SCHEMA_HASH, "result": _view(existing)}
    _live_schema(service)
    digest = hashlib.sha256(canonical({"schemaHash": SCHEMA_HASH, "method": "DELETE", "path": path,
                                       "uid": uid, "resourceVersion": resource_version,
                                       "marker": _marker(args)})).hexdigest()
    if args["phase"] == "plan":
        return {"state": "ready", "planHash": digest, "schemaHash": SCHEMA_HASH,
                "target": {"name": args["name"], "uid": uid, "resourceVersion": resource_version}}
    if args.get("planHash") != digest:
        raise OperationError("PLUGIN_PRECONDITION_FAILED", "Cilium canary changed; plan deletion again")
    body = {"apiVersion": "meta.k8s.io/v1", "kind": "DeleteOptions",
            "preconditions": {"uid": uid, "resourceVersion": resource_version}}
    try:
        response = service._plugin_http_request("DELETE", path, canonical(body))
    except OperationError:
        observed = _read(service, path)
        if observed is None:
            return {"state": "recovered_absent", "planHash": digest, "targetUid": uid}
        raise OperationError("PLUGIN_RESULT_UNKNOWN", "Delete response was lost; query status before retrying")
    _acknowledge(response, DELETE_TOOL["operationId"], {200, 202})
    observed = _read(service, path)
    if observed and observed.get("metadata", {}).get("uid") != uid:
        raise OperationError("PLUGIN_RESULT_UNKNOWN", "A different CiliumCIDRGroup occupies the name")
    return {"state": "deleting" if observed else "deleted", "httpStatus": response["status"],
            "planHash": digest, "targetUid": uid}
