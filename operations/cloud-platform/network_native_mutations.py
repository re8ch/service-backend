"""Fixed AdvFab native mutation catalog and bounded execution adapter."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from jsonschema import Draft202012Validator
from runtime import OperationError

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1] if len(HERE.parents) > 1 else HERE
MANIFEST = HERE / "network-planner/operations.json"
SCHEMAS = {
    "apis/networking.advfab.org/v1alpha1": "apis_networking.advfab.org_v1alpha1.json",
    "apis/networking.advfab.org/v1alpha2": "apis_networking.advfab.org_v1alpha2.json",
    "apis/networking.re8ch.com/v1alpha1": "apis_networking.re8ch.com_v1alpha1.json",
    "apis/networking.re8ch.com/v1alpha2": "apis_networking.re8ch.com_v1alpha2.json",
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def build_manifest():
    matrix = json.loads((ROOT / "operations/plugin-capabilities/native-operation-matrix.json").read_text())
    rows = []
    for source in matrix["sources"]:
        group = source["groupVersion"]
        if group not in SCHEMAS:
            continue
        document = json.loads((ROOT / "operations/plugin-capabilities/openapi" / SCHEMAS[group]).read_text())
        digest = hashlib.sha256(canonical(document)).hexdigest()
        for row in source["operations"]:
            method, path, kind = row["method"].upper(), row["path"], row["kind"]
            if method not in {"POST", "PUT", "PATCH", "DELETE"} or kind == "RouterOSNode" or path.endswith("/status") or (method == "DELETE" and "{name}" not in path):
                continue
            if "{name}" not in path and method != "POST":
                continue
            plugin = "advfab" if group.startswith("apis/networking.advfab.org/") else ("advfab-legacy" if kind not in {"MobileEgress", "PublicEdge"} else kind.lower().replace("egress", "-egress").replace("edge", "-edge"))
            identity = group + ":" + row["operationId"]
            suffix = hashlib.sha256(identity.encode()).hexdigest()[:12]
            rows.append({"name": f"{plugin}.{kind.lower()}.{method.lower()}.{suffix}",
                         "plugin": plugin, "groupVersion": group, "method": method, "path": path,
                         "operationId": row["operationId"], "kind": kind, "schemaHash": digest})
    return sorted(rows, key=lambda row: row["name"])


def tool_definitions(rows):
    tools = []
    for row in rows:
        properties = {"phase": {"type": "string", "enum": ["plan", "execute", "status"]},
                      "name": {"type": "string", "pattern": "^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$"},
                      "idempotencyKey": {"type": "string", "minLength": 8, "maxLength": 128},
                      "planHash": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}
        required = ["phase", "name", "idempotencyKey"]
        if "{namespace}" in row["path"]:
            properties["namespace"] = {"type": "string", "pattern": properties["name"]["pattern"]}
            required.append("namespace")
        if row["method"] != "DELETE":
            properties["body"] = {"type": "object"}
            required.append("body")
        tools.append({**row,
            "description": f"Plan, execute, or inspect exact native {row['method']} {row['path']}. Administrator only; target UID/resourceVersion and schema are bound. Result is metadata only.",
            "inputSchema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
            "annotations": {"readOnlyHint": False, "destructiveHint": row["method"] == "DELETE", "idempotentHint": True},
            "_meta": {"re8ch": {"plugin": row["plugin"], "groupVersion": row["groupVersion"], "operationId": row["operationId"],
                                "method": row["method"], "path": row["path"], "schemaHash": row["schemaHash"],
                                "httpConformance": "not_run", "semanticConformance": "not_run"}}})
    return tools


MANIFEST_ROWS = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else []
TOOLS = tool_definitions(MANIFEST_ROWS)
BY_NAME = {row["name"]: row for row in TOOLS}


def _read(service, path):
    response = service._plugin_http_get(path)
    if response["status"] == 404:
        return None
    if response["status"] != 200:
        raise OperationError("PLUGIN_READ_FAILED", "Native target preflight failed")
    try:
        value = json.loads(response["body"])
    except (ValueError, TypeError) as exc:
        raise OperationError("PLUGIN_READ_FAILED", "Native target was not JSON") from exc
    if not isinstance(value, dict):
        raise OperationError("PLUGIN_READ_FAILED", "Native target was not an object")
    return value


def _view(value):
    if value is None:
        return None
    meta = value.get("metadata", {})
    return {key: meta.get(key) for key in ("name", "namespace", "uid", "resourceVersion", "generation")}


def _same_create(existing, args):
    marker = hashlib.sha256(args["idempotencyKey"].encode()).hexdigest()
    return (existing.get("kind") == args["body"].get("kind")
            and existing.get("apiVersion") == args["body"].get("apiVersion")
            and existing.get("spec") == args["body"].get("spec")
            and existing.get("metadata", {}).get("annotations", {}).get("operations.re8ch.com/idempotency-hash") == marker)


def call(service, tool_name, args):
    from plugin_capabilities import openapi, operation_contract, validate_response_body, local_ref
    tool = BY_NAME.get(tool_name)
    if tool is None:
        raise OperationError("PLUGIN_OPERATION_NOT_FOUND", "Unknown fixed network operation")
    if list(Draft202012Validator(tool["inputSchema"]).iter_errors(args)):
        raise OperationError("INVALID_ARGUMENT", "Arguments do not match the fixed native operation")
    if ".." in args["name"] or ".." in args.get("namespace", ""):
        raise OperationError("INVALID_ARGUMENT", "Native path segments cannot contain dot traversal")
    if "body" in args and len(canonical(args["body"])) > 4 * 1024 * 1024:
        raise OperationError("INVALID_ARGUMENT", "Native request body exceeds the transport bound")
    if "body" in args and "status" in args["body"]:
        raise OperationError("INVALID_ARGUMENT", "Main-resource tools cannot write controller status")
    if openapi(service, tool["groupVersion"])["sha256"] != tool["schemaHash"]:
        raise OperationError("PLUGIN_SCHEMA_CHANGED", "Refresh the reviewed network contract")
    collection = tool["path"].split("/{name}")[0]
    if tool["method"] == "POST":
        target = collection + "/" + args["name"]
    else:
        target = tool["path"]
    if "{namespace}" in target:
        target = target.replace("{namespace}", args["namespace"])
    target = target.replace("{name}", args["name"])
    existing = _read(service, target)
    if args["phase"] == "status":
        return {"state": "present" if existing else "absent", "target": _view(existing), "schemaHash": tool["schemaHash"]}
    if tool["method"] == "POST" and existing is not None:
        if _same_create(existing, args):
            return {"state": "already_created", "target": _view(existing), "schemaHash": tool["schemaHash"]}
        raise OperationError("PLUGIN_OPERATION_CONFLICT", "Native target already exists")
    if tool["method"] != "POST" and existing is None:
        if tool["method"] == "DELETE":
            return {"state": "absent", "schemaHash": tool["schemaHash"]}
        raise OperationError("PLUGIN_OPERATION_CONFLICT", "Native target does not exist")
    planner = HERE / "network-planner/network-planner"
    input_value = {"tool": tool_name, "name": args["name"], "namespace": args.get("namespace", ""),
                   "idempotencyKey": args["idempotencyKey"]}
    if "body" in args:
        input_value["body"] = args["body"]
    if existing is not None:
        input_value["existing"] = existing
    try:
        result = subprocess.run([str(planner)], input=canonical(input_value), capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OperationError("PLUGIN_PLANNER_UNAVAILABLE", "Fixed network planner is unavailable") from exc
    if result.returncode:
        raise OperationError("PLUGIN_PRECONDITION_FAILED", "Native operation target or body failed validation")
    plan = json.loads(result.stdout)
    if plan["path"] != (collection if tool["method"] == "POST" else target):
        raise OperationError("PLUGIN_PRECONDITION_FAILED", "Planner returned an unexpected path")
    schema_file = HERE / SCHEMAS[tool["groupVersion"]]
    if not schema_file.exists():
        schema_file = ROOT / "operations/plugin-capabilities/openapi" / SCHEMAS[tool["groupVersion"]]
    doc = json.loads(schema_file.read_text())
    contract = operation_contract(doc, tool["operationId"])
    request_body = local_ref(doc, contract["requestBody"] or {})
    request_schema = request_body.get("content", {}).get("application/json", {}).get("schema")
    if isinstance(request_schema, dict) and plan.get("body") is not None:
        validate_response_body(doc, {"responses": {"200": {"content": {"application/json": {"schema": request_schema}}}}}, plan["body"], False)
    if args["phase"] == "plan":
        return {"state": "ready", "planHash": plan["planHash"], "schemaHash": tool["schemaHash"],
                "target": {"name": args["name"], "namespace": args.get("namespace"), "uid": plan.get("uid"), "resourceVersion": plan.get("resourceVersion")}}
    if args.get("planHash") != plan["planHash"]:
        raise OperationError("PLUGIN_PRECONDITION_FAILED", "Native target changed; plan again")
    body = canonical(plan["body"]) if plan.get("body") is not None else None
    try:
        response = service._plugin_http_request(tool["method"], plan["path"], body,
                                                 "application/merge-patch+json" if tool["method"] == "PATCH" else "application/json")
    except OperationError as exc:
        raise OperationError("PLUGIN_RESULT_UNKNOWN", "Native write response was lost; query status") from exc
    if response["status"] not in {200, 201, 202}:
        raise OperationError("PLUGIN_RESULT_UNKNOWN", "Native write returned an unexpected HTTP status; query status")
    if not any(value.split(";", 1)[0].strip().lower() == "application/json"
               for value in response.get("headers", {}).get("content-type", [])):
        raise OperationError("PLUGIN_RESULT_UNKNOWN", "Native write response was not JSON; query status")
    try:
        observed_body = json.loads(response["body"])
        validate_response_body(doc, contract, observed_body, False)
    except (OperationError, ValueError, TypeError) as exc:
        raise OperationError("PLUGIN_RESULT_UNKNOWN", "Native write response contract failed; query status") from exc
    observed = _read(service, target)
    if tool["method"] == "DELETE":
        if observed is not None and _view(observed)["uid"] != plan["uid"]:
            raise OperationError("PLUGIN_RESULT_UNKNOWN", "A different resource occupies the name")
    elif observed is None:
        raise OperationError("PLUGIN_RESULT_UNKNOWN", "Native write effect was not observed")
    elif tool["method"] == "POST" and not _same_create(observed, args):
        raise OperationError("PLUGIN_RESULT_UNKNOWN", "Created resource differs from the planned request")
    elif tool["method"] != "POST" and _view(observed)["uid"] != plan["uid"]:
        raise OperationError("PLUGIN_RESULT_UNKNOWN", "Native resource identity changed after write")
    state = "deleted" if tool["method"] == "DELETE" and observed is None else "deleting" if tool["method"] == "DELETE" else "observed"
    return {"state": state, "httpStatus": response["status"],
            "planHash": plan["planHash"], "target": _view(observed), "schemaHash": tool["schemaHash"]}
