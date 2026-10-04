"""Bounded RuntimeDiff create operation through the native Kubernetes HTTP API."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote

from runtime import OperationError

GROUP = "apis/anatomy.re8ch.com/v1alpha1"
NAMESPACE = "runtime-anatomy-canary"
NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
ANNOTATION = "operations.re8ch.com/idempotency-hash"
SCHEMA_FILE = Path(__file__).with_name("anatomy-native-openapi.json")
if not SCHEMA_FILE.exists():
    SCHEMA_FILE = Path(__file__).resolve().parents[1] / "plugin-capabilities/openapi/apis_anatomy.re8ch.com_v1alpha1.json"
DOCUMENT = json.loads(SCHEMA_FILE.read_text())
SCHEMA_HASH = hashlib.sha256(json.dumps(DOCUMENT, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

TOOL = {
    "name": "anatomy.runtimediff.create",
    "plugin": "anatomy", "groupVersion": GROUP,
    "operationId": "createAnatomyRe8chComV1alpha1NamespacedRuntimeDiff",
    "schemaHash": SCHEMA_HASH,
    "description": "Plan, create, or inspect one RuntimeDiff in the bounded canary namespace. Administrator only.",
    "inputSchema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "phase": {"type": "string", "enum": ["plan", "execute", "status"]},
            "namespace": {"type": "string", "const": NAMESPACE},
            "name": {"type": "string", "pattern": NAME.pattern},
            "baseCommit": {"type": "string", "pattern": NAME.pattern},
            "targetCommit": {"type": "string", "pattern": NAME.pattern},
            "idempotencyKey": {"type": "string", "minLength": 8, "maxLength": 128},
            "planHash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        },
        "required": ["phase", "namespace", "name", "baseCommit", "targetCommit", "idempotencyKey"],
    },
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    "_meta": {"re8ch": {"plugin": "anatomy", "groupVersion": GROUP,
                        "operationId": "createAnatomyRe8chComV1alpha1NamespacedRuntimeDiff",
                        "method": "POST", "path": f"/{GROUP}/namespaces/{{namespace}}/runtimediffs",
                        "schemaHash": SCHEMA_HASH,
                        "httpConformance": "not_run", "semanticConformance": "not_run"}},
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def read(service, path):
    response = service._plugin_http_get(path)
    if response["status"] == 404:
        return None
    if response["status"] != 200:
        raise OperationError("PLUGIN_READ_FAILED", "Native preflight read failed")
    try:
        return json.loads(response["body"])
    except (ValueError, TypeError) as exc:
        raise OperationError("PLUGIN_READ_FAILED", "Native preflight response is not JSON") from exc


def result_view(obj):
    metadata = obj.get("metadata", {})
    status = obj.get("status", {})
    return {"name": metadata.get("name"), "namespace": metadata.get("namespace"),
            "uid": metadata.get("uid"), "resourceVersion": metadata.get("resourceVersion"),
            "phase": status.get("phase"), "addedCount": len(status.get("added", [])),
            "removedCount": len(status.get("removed", [])),
            "modifiedCount": len(status.get("modified", []))}


def call(service, args):
    from jsonschema import Draft202012Validator
    from plugin_capabilities import openapi

    if list(Draft202012Validator(TOOL["inputSchema"]).iter_errors(args)):
        raise OperationError("INVALID_ARGUMENT", "RuntimeDiff arguments do not match the tool schema")
    if args["namespace"] != NAMESPACE:
        raise OperationError("AUTHORIZATION_DENIED", "RuntimeDiff writes are limited to the canary namespace")
    for field in ("name", "baseCommit", "targetCommit"):
        if not NAME.fullmatch(args[field]):
            raise OperationError("INVALID_ARGUMENT", f"Invalid {field}")
    phase = args["phase"]
    ns = quote(args["namespace"], safe="")
    name = quote(args["name"], safe="")
    collection = f"/{GROUP}/namespaces/{ns}/runtimediffs"
    object_path = f"{collection}/{name}"
    marker = hashlib.sha256(args["idempotencyKey"].encode()).hexdigest()
    existing = read(service, object_path)
    if phase == "status":
        if existing is None:
            return {"state": "not_found", "schemaHash": SCHEMA_HASH}
        if existing.get("metadata", {}).get("annotations", {}).get(ANNOTATION) != marker:
            raise OperationError("PLUGIN_OPERATION_CONFLICT", "Object belongs to another request")
        if existing.get("spec") != {"baseRef": {"name": args["baseCommit"]},
                                     "targetRef": {"name": args["targetCommit"]}}:
            raise OperationError("PLUGIN_OPERATION_CONFLICT", "Object request parameters changed")
        return {"state": "observed", "schemaHash": SCHEMA_HASH, "result": result_view(existing)}

    live = openapi(service, GROUP)
    if live["sha256"] != SCHEMA_HASH:
        raise OperationError("PLUGIN_SCHEMA_CHANGED", "Refresh the reviewed RuntimeDiff contract")
    commits = []
    for key in ("baseCommit", "targetCommit"):
        path = f"/{GROUP}/namespaces/{ns}/runtimecommits/{quote(args[key], safe='')}"
        commit = read(service, path)
        if commit is None or commit.get("status", {}).get("phase") != "Sealed":
            raise OperationError("PLUGIN_PRECONDITION_FAILED", "Both commit references must be Sealed")
        metadata = commit.get("metadata", {})
        if not metadata.get("uid") or not metadata.get("resourceVersion"):
            raise OperationError("PLUGIN_PRECONDITION_FAILED", "Commit identity is incomplete")
        commits.append({"name": args[key], "uid": metadata.get("uid"),
                        "resourceVersion": metadata.get("resourceVersion")})
    body = {"apiVersion": "anatomy.re8ch.com/v1alpha1", "kind": "RuntimeDiff",
            "metadata": {"name": args["name"], "namespace": args["namespace"],
                         "annotations": {ANNOTATION: marker}},
            "spec": {"baseRef": {"name": args["baseCommit"]},
                     "targetRef": {"name": args["targetCommit"]}}}
    digest = hashlib.sha256(canonical({"schemaHash": SCHEMA_HASH, "body": body, "commits": commits})).hexdigest()
    if existing is not None:
        if existing.get("metadata", {}).get("annotations", {}).get(ANNOTATION) == marker and existing.get("spec") == body["spec"]:
            return {"state": "already_created", "planHash": digest, "result": result_view(existing)}
        raise OperationError("PLUGIN_OPERATION_CONFLICT", "RuntimeDiff name is already in use")
    if phase == "plan":
        return {"state": "ready", "planHash": digest, "schemaHash": SCHEMA_HASH,
                "target": {"namespace": args["namespace"], "name": args["name"]},
                "commitPreconditions": commits}
    if args.get("planHash") != digest:
        raise OperationError("PLUGIN_PRECONDITION_FAILED", "Plan or commit versions changed; plan again")
    try:
        response = service._plugin_http_request("POST", collection, canonical(body))
    except OperationError:
        recovered = read(service, object_path)
        if recovered and recovered.get("metadata", {}).get("annotations", {}).get(ANNOTATION) == marker and recovered.get("spec") == body["spec"]:
            return {"state": "recovered", "planHash": digest, "result": result_view(recovered)}
        raise OperationError("PLUGIN_RESULT_UNKNOWN", "Write response was lost; query status before retrying")
    if response["status"] not in {200, 201, 202}:
        raise OperationError("PLUGIN_WRITE_FAILED", f"Native create returned HTTP {response['status']}")
    content_type = response.get("headers", {}).get("content-type", [])
    if not any(value.split(";", 1)[0].strip().lower() == "application/json" for value in content_type):
        raise OperationError("PLUGIN_RESULT_UNKNOWN", "Create response was not JSON; query status")
    try:
        acknowledged = json.loads(response["body"])
    except (ValueError, TypeError) as exc:
        raise OperationError("PLUGIN_RESULT_UNKNOWN", "Create response was invalid; query status") from exc
    if acknowledged.get("metadata", {}).get("name") != args["name"]:
        raise OperationError("PLUGIN_RESULT_UNKNOWN", "Create response target did not match; query status")
    observed = read(service, object_path)
    if not observed or observed.get("metadata", {}).get("annotations", {}).get(ANNOTATION) != marker:
        raise OperationError("PLUGIN_RESULT_UNKNOWN", "Create returned success but target could not be confirmed")
    return {"state": "created", "httpStatus": response["status"], "planHash": digest,
            "result": result_view(observed)}
