"""Mihomos cluster evidence from fixed, namespace-scoped Kubernetes resources."""
import json
import hashlib
import re
from pathlib import Path
from urllib.parse import quote

from jsonschema import Draft202012Validator
from runtime import OperationError

WORKLOADS = frozenset({"mihomos-node-cn", "mihomos-node-global", "mihomos-cn-exit", "mihomos-global-exit", "mihomos-status-agent", "mihomos-anytls-public-edge", "mihomos-mobile-publication"})
COUNTERS = ("desiredNumberScheduled", "currentNumberScheduled", "numberReady", "numberAvailable", "numberUnavailable", "numberMisscheduled", "updatedNumberScheduled", "observedGeneration")


def workloads(service):
    result = service._kubectl(["get", "daemonsets", "-n", "egress-fabric", "-o", "json"])
    if result.get("exitCode") != 0 or result.get("truncated"):
        raise OperationError("MIHOMOS_DISCOVERY_FAILED", "Workload discovery failed or was truncated")
    try:
        body = json.loads(result["stdout"])
        if not isinstance(body.get("items"), list):
            raise ValueError("Expected a resource list")
        rows = []
        for item in body["items"]:
            meta = item["metadata"]
            if meta.get("name") not in WORKLOADS or meta.get("namespace") != "egress-fabric":
                continue
            status = item.get("status", {})
            counters = {key: status[key] for key in COUNTERS if key in status}
            if any(type(value) is not int or value < 0 for value in counters.values()):
                raise ValueError("Invalid status counter")
            rows.append({"name": meta["name"], "namespace": "egress-fabric", "uid": meta.get("uid"),
                         "generation": meta.get("generation"), "status": counters})
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise OperationError("MIHOMOS_DISCOVERY_FAILED", "Invalid workload evidence") from exc
    return {"cluster": "re8ch-k3s", "namespace": "egress-fabric", "workloads": sorted(rows, key=lambda row: row["name"]),
            "missingWorkloads": sorted(WORKLOADS - {row["name"] for row in rows}), "auditRef": result.get("auditRef"),
            "coverage": "workload_status_only"}

TOOL = {"name": "mihomos.workloads.list", "description": "Read fixed Mihomos DaemonSet status in egress-fabric. Administrator only. Returns readiness counters and missing workloads; excludes config, environment and credentials. Workload status only, not controller API coverage.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False, "required": []}}

CONTROLLER_OWNERS = frozenset({"mihomos-node-cn", "mihomos-node-global"})


def _read(service, argv):
    result = service._kubectl(argv)
    if result.get("exitCode") != 0 or result.get("truncated"):
        raise OperationError("MIHOMOS_CONTROLLER_UNAVAILABLE", "Controller discovery/read failed or was truncated")
    try:
        value = json.loads(result["stdout"])
        if not isinstance(value, dict):
            raise ValueError("Expected object")
    except (ValueError, KeyError) as exc:
        raise OperationError("MIHOMOS_CONTROLLER_UNAVAILABLE", "Invalid controller evidence") from exc
    return value, result.get("auditRef")


def _target(pod):
    """Only reviewed node-proxy owners and their declared controller port qualify."""
    meta, spec, status = pod.get("metadata", {}), pod.get("spec", {}), pod.get("status", {})
    owners = meta.get("ownerReferences", [])
    if meta.get("namespace") != "egress-fabric" or meta.get("deletionTimestamp"):
        return None
    if not any(owner.get("controller") is True and owner.get("kind") == "DaemonSet" and owner.get("name") in CONTROLLER_OWNERS for owner in owners):
        return None
    if meta.get("labels", {}).get("app.kubernetes.io/component") != "node-proxy":
        return None
    if not any(container.get("name") == "mihomo" and any(port.get("name") == "controller" and port.get("containerPort") == 19090 for port in container.get("ports", [])) for container in spec.get("containers", [])):
        return None
    ready = status.get("phase") == "Running" and any(condition.get("type") == "Ready" and condition.get("status") == "True" for condition in status.get("conditions", []))
    return {"pod": meta.get("name"), "uid": meta.get("uid"), "node": spec.get("nodeName"), "ready": ready}


def controllers(service):
    body, audit = _read(service, ["get", "pods", "-n", "egress-fabric", "-l", "app.kubernetes.io/component=node-proxy", "-o", "json", "--request-timeout=10s"])
    try:
        if not isinstance(body.get("items"), list):
            raise ValueError("Expected pod list")
        targets = [target for pod in body["items"] if (target := _target(pod)) is not None]
        targets.sort(key=lambda target: target["pod"])
    except (ValueError, TypeError, AttributeError) as exc:
        raise OperationError("MIHOMOS_CONTROLLER_UNAVAILABLE", "Invalid pod discovery evidence") from exc
    return {"cluster": "re8ch-k3s", "namespace": "egress-fabric", "controllers": targets, "auditRef": audit}


def _checked_target(service, pod, uid):
    if not isinstance(pod, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", pod) or ".." in pod:
        raise OperationError("INVALID_ARGUMENT", "Use a pod returned by mihomos.controllers.list")
    if not isinstance(uid, str) or not re.fullmatch(r"[a-f0-9-]{36}", uid):
        raise OperationError("INVALID_ARGUMENT", "Use the discovered pod UID")
    body, discovery_audit = _read(service, ["get", "pod", pod, "-n", "egress-fabric", "-o", "json", "--request-timeout=10s"])
    try:
        target = _target(body)
    except (TypeError, AttributeError) as exc:
        raise OperationError("MIHOMOS_CONTROLLER_UNAVAILABLE", "Invalid pod evidence") from exc
    if target is None or target["pod"] != pod or target["uid"] != uid or not target["ready"]:
        raise OperationError("MIHOMOS_TARGET_CHANGED", "Discover a current Ready controller before calling")
    return target, discovery_audit


def _project(value, schema):
    """Return declared evidence fields, omitting URLs and extensible adapter data."""
    if "anyOf" in schema:
        schema = next(branch for branch in schema["anyOf"] if Draft202012Validator(CONTROLLER_OPENAPI).evolve(schema=branch).is_valid(value))
    if "$ref" in schema:
        schema = CONTROLLER_OPENAPI["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    if isinstance(value, list):
        return [_project(item, schema.get("items", {})) for item in value]
    if isinstance(value, dict):
        if isinstance(schema.get("additionalProperties"), dict):
            return {key: _project(item, schema["additionalProperties"]) for key, item in value.items()}
        properties = schema.get("properties", {})
        return {key: _project(item, properties[key]) for key, item in value.items()
                if key in properties and key not in {"testUrl", "expectedStatus"}}
    return value


def controller_read(service, pod, uid, operation_id, path_parameters=None):
    if operation_id not in READ_OPERATIONS:
        raise OperationError("MIHOMOS_OPERATION_NOT_READ_ONLY", "Use a published reviewed controller read")
    path, operation = READ_OPERATIONS[operation_id]
    parameters = {} if path_parameters is None else path_parameters
    required = {parameter["name"] for parameter in operation.get("parameters", [])}
    if not isinstance(parameters, dict) or set(parameters) != required:
        raise OperationError("INVALID_ARGUMENT", "Native path parameters do not match the operation")
    for name, value in parameters.items():
        if not isinstance(value, str) or not value or len(value) > 256 or value in {".", ".."} or any(char in value for char in "/\\%?#") or any(ord(char) < 32 for char in value):
            raise OperationError("INVALID_ARGUMENT", "Native names must be non-empty safe path segments")
        path = path.replace("{" + name + "}", quote(value, safe=""))
    target, discovery_audit = _checked_target(service, pod, uid)
    native_path = f"/api/v1/namespaces/egress-fabric/pods/{pod}:19090/proxy" + path
    value, audit = _read(service, ["get", "--raw", native_path, "--request-timeout=10s"])
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    if list(Draft202012Validator(CONTROLLER_OPENAPI).evolve(schema=response_schema).iter_errors(value)):
        raise OperationError("MIHOMOS_CONTRACT_FAILED", "Response does not match the native read contract")
    return {"cluster": "re8ch-k3s", "target": target, "operationId": operation_id, "result": _project(value, response_schema),
            "auditRef": audit, "discoveryAuditRef": discovery_audit}


def controller_version(service, pod, uid):
    return controller_read(service, pod, uid, "getMihomoVersion")


CONTROLLER_OPENAPI = json.loads(Path(__file__).with_name("mihomo-controller-openapi.json").read_text())
READ_OPERATIONS = {operation["operationId"]: (path, operation)
                   for path, item in CONTROLLER_OPENAPI["paths"].items()
                   for method, operation in item.items() if method == "get"}
CONTROLLER_SCHEMA_HASH = hashlib.sha256(json.dumps(CONTROLLER_OPENAPI, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
TOOLS = [TOOL, {"name": "mihomos.controllers.list", "description": "Discover reviewed Mihomos node-proxy controller pods and readiness. Administrator only; returns pod UID for subsequent calls.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False, "required": []}}]
for operation_id, (path, operation) in READ_OPERATIONS.items():
    properties = {"pod": {"type": "string"}, "uid": {"type": "string", "pattern": "^[a-f0-9-]{36}$"}}
    required = ["pod", "uid"]
    parameters = operation.get("parameters", [])
    if parameters:
        properties["path"] = {"type": "object", "properties": {item["name"]: item["schema"] for item in parameters},
                              "required": [item["name"] for item in parameters], "additionalProperties": False}
        required.append("path")
    description = (f"Read native Mihomo GET {path} through a validated Ready pod and UID. Administrator only. "
                   "Source-derived partial contract; output omits URLs and extensible adapter fields. "
                   + operation.get("description", ""))
    TOOLS.append({"name": operation["x-re8ch-tool"], "description": description.strip(),
                  "inputSchema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
                  "annotations": {"readOnlyHint": True, "destructiveHint": False},
                  "_meta": {"re8ch": {"plugin": "mihomos-cluster", "groupVersion": "controller/v1.19.29", "operationId": operation_id,
                                      "method": "GET", "path": path, "schemaHash": CONTROLLER_SCHEMA_HASH,
                                      "httpConformance": "not_run", "semanticConformance": "not_run"}}})
