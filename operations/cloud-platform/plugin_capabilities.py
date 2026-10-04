"""Native Kubernetes OpenAPI evidence for plugin capability registration.

Preserves native schemas and exposes reviewed, bounded administrator reads.
Coverage of mutations and non-Kubernetes APIs is tracked separately.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from runtime import OperationError

GROUP_PLUGINS = {
    "cilium.io": "cilium",
    "networking.advfab.org": "advfab",
    "anatomy.re8ch.com": "anatomy",
    "ceph.rook.io": "rook-ceph",
    "csi.ceph.io": "rook-ceph-csi",
    "objectbucket.io": "object-bucket",
}
RESOURCE_PLUGINS = {"PublicEdge": "public-edge", "MobileEgress": "mobile-egress"}
ADVFAB_KINDS = {"AdvancedFabric", "NetworkIntervention", "NetworkObservationEpisode", "NetworkPathAssessment", "NetworkTradeoffEstimate", "RouterOSNode", "TradeoffTriangle", "TrafficPolicy"}
METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
VERSION_KEY = re.compile(r"^(?:api/[a-z0-9]+|apis/[a-z0-9.-]+/[a-z0-9]+)$")


def read_json(service: Any, path: str) -> tuple[dict, str]:
    result = service._kubectl(["get", "--raw", path])
    if result.get("exitCode") != 0 or result.get("truncated"):
        raise OperationError("PLUGIN_DISCOVERY_FAILED", "Native schema discovery failed or was truncated")
    try:
        body = json.loads(result["stdout"])
    except (ValueError, KeyError) as exc:
        raise OperationError("PLUGIN_DISCOVERY_FAILED", "Native schema is not JSON") from exc
    if not isinstance(body, dict):
        raise OperationError("PLUGIN_DISCOVERY_FAILED", "Native schema must be an object")
    return body, result.get("auditRef", "")


def catalog(service: Any) -> dict:
    index, audit = read_json(service, "/openapi/v3")
    paths = index.get("paths")
    if not isinstance(paths, dict):
        raise OperationError("PLUGIN_DISCOVERY_FAILED", "Missing OpenAPI discovery paths")
    entries = []
    for key in sorted(paths):
        if not VERSION_KEY.fullmatch(key):
            continue
        group = key.split("/")[1] if key.startswith("apis/") else "core"
        entries.append({"groupVersion": key, "plugin": GROUP_PLUGINS.get(group, "unclassified"),
                        "schemaTool": "plugin.openapi.get", "arguments": {"groupVersion": key}})
    return {"cluster": "re8ch-k3s", "source": "Kubernetes /openapi/v3", "schemas": entries,
            "auditRef": audit, "coverage": "discovery_only",
            "uncoveredSurfaces": ["non-Kubernetes native APIs", "management UI semantic workflows", "other connected clusters"]}


def openapi(service: Any, group_version: str) -> dict:
    if not isinstance(group_version, str) or not VERSION_KEY.fullmatch(group_version):
        raise OperationError("INVALID_ARGUMENT", "groupVersion must be an exact catalog key")
    index, _ = read_json(service, "/openapi/v3")
    entry = index.get("paths", {}).get(group_version)
    if not isinstance(entry, dict):
        raise OperationError("PLUGIN_SCHEMA_NOT_FOUND", group_version)
    location = entry.get("serverRelativeURL", "")
    if not isinstance(location, str):
        raise OperationError("PLUGIN_DISCOVERY_FAILED", "Invalid schema URL")
    parsed = urlsplit(location)
    # A discovery response cannot redirect this tool to arbitrary Kubernetes data
    # or an external host. Preserve the server's schema hash query if present.
    if parsed.scheme or parsed.netloc or parsed.fragment or parsed.path != "/openapi/v3/" + group_version:
        raise OperationError("PLUGIN_DISCOVERY_FAILED", "Unexpected native schema URL")
    document, audit = read_json(service, location)
    if not str(document.get("openapi", "")).startswith("3.") or not isinstance(document.get("paths"), dict):
        raise OperationError("PLUGIN_DISCOVERY_FAILED", "Expected native OpenAPI 3 document")
    group = group_version.split("/")[1] if group_version.startswith("apis/") else "core"
    operations = []
    for path, item in sorted(document["paths"].items()):
        for method, operation in sorted(item.items()):
            if method not in METHODS or not isinstance(operation, dict):
                continue
            gvk = operation.get("x-kubernetes-group-version-kind", {})
            kind = gvk.get("kind", "")
            plugin = GROUP_PLUGINS.get(group, RESOURCE_PLUGINS.get(kind, "unclassified"))
            if group == "networking.re8ch.com" and kind in ADVFAB_KINDS:
                plugin = "advfab-legacy"
            operations.append({"plugin": plugin, "method": method.upper(), "path": path,
                               "operationId": operation.get("operationId"), "kind": kind,
                               "nativeAction": operation.get("x-kubernetes-action"),
                               "registration": "not_verified", "schemaConformance": "not_run",
                               "semanticConformance": "not_run"})
    digest = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"cluster": "re8ch-k3s", "groupVersion": group_version, "source": location,
            "sha256": digest, "auditRef": audit, "operations": operations, "openapi": document,
            "coverage": "native_schema_only"}

# Resource reads are reviewed independently of schema discovery. Discovering a
# new API group does not grant the platform backend access to its objects.
READ_GROUPS = {
    "apis/anatomy.re8ch.com/v1alpha1", "apis/ceph.rook.io/v1",
    "apis/csi.ceph.io/v1", "apis/objectbucket.io/v1alpha1",
    "apis/networking.advfab.org/v1alpha1", "apis/networking.advfab.org/v1alpha2",
    "apis/networking.re8ch.com/v1alpha1", "apis/networking.re8ch.com/v1alpha2",
}

# Cilium's broad CRD groups also contain endpoint and Envoy configuration data.
# Review these BGP reads individually rather than enabling every GET in a group.
CILIUM_BGP_READS = frozenset(
    (f"apis/cilium.io/{version}", f"{verb}CiliumIo{native_version}CiliumBGP{kind}{suffix}")
    for version, native_version in (("v2", "V2"), ("v2alpha1", "V2alpha1"))
    for kind, suffixes in (
        ("Advertisement", ("",)),
        ("ClusterConfig", ("", "Status")),
        ("NodeConfigOverride", ("",)),
        ("NodeConfig", ("", "Status")),
        ("PeerConfig", ("", "Status")),
    )
    for suffix in suffixes
    for verb in (("list", "read") if not suffix else ("read",))
)


def reviewed_read(group_version: str, operation_id: str) -> bool:
    return group_version in READ_GROUPS or (group_version, operation_id) in CILIUM_BGP_READS


def local_ref(document: dict, value: dict) -> dict:
    seen = set()
    while "$ref" in value:
        ref = value["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
            raise OperationError("PLUGIN_SCHEMA_UNSUPPORTED", "Only non-cyclic local parameter references are supported")
        seen.add(ref)
        value = document
        try:
            for segment in ref[2:].split("/"):
                value = value[segment.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError) as exc:
            raise OperationError("PLUGIN_SCHEMA_UNSUPPORTED", "Unresolved parameter reference") from exc
    return value


ANATOMY_AUTHORITY = json.loads(Path(__file__).with_name("anatomy-operation-authority.json").read_text())


def operation_disposition(group_version: str, kind: str, method: str, path: str) -> str:
    """Classify source semantics without granting any mutation capability."""
    if method.lower() == "get":
        return "read"
    if path.endswith("/status"):
        return "controller_owned"
    if group_version == ANATOMY_AUTHORITY["groupVersion"]:
        return ANATOMY_AUTHORITY["kinds"].get(kind, {}).get("disposition", "authority_review_required")
    return "authority_review_required"


def operation_contract(document: dict, operation_id: str) -> dict:
    matches = [(path, method, item, operation)
               for path, item in document["paths"].items()
               for method, operation in item.items()
               if method in METHODS and isinstance(operation, dict) and operation.get("operationId") == operation_id]
    if len(matches) != 1:
        raise OperationError("PLUGIN_OPERATION_NOT_FOUND", "Expected exactly one native operationId")
    path, method, item, operation = matches[0]
    properties = {location: {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
                  for location in ("path", "query")}
    # Operation parameters override path-item parameters with the same name/in.
    parameters = {}
    for parameter in item.get("parameters", []) + operation.get("parameters", []):
        parameter = local_ref(document, parameter)
        parameters[(parameter["in"], parameter["name"])] = parameter
    for (location, name), parameter in parameters.items():
        if location not in properties:
            continue
        schema = local_ref(document, parameter.get("schema", {}))
        if schema.get("type") not in {"string", "integer", "number", "boolean"}:
            raise OperationError("PLUGIN_SCHEMA_UNSUPPORTED", "Native parameter is not a supported scalar")
        properties[location]["properties"][name] = schema
        if parameter.get("required"):
            properties[location]["required"].append(name)
    for location in properties:
        properties[location]["required"].sort()
    schema = {"type": "object", "properties": properties, "additionalProperties": False,
              "required": [location for location in properties if properties[location]["required"]]}
    gvk = operation.get("x-kubernetes-group-version-kind", {})
    group_version = "apis/" + gvk.get("group", "") + "/" + gvk.get("version", "")
    disposition = operation_disposition(group_version, gvk.get("kind", ""), method, path)
    return {"operationId": operation_id, "method": method.upper(), "pathTemplate": path,
            "inputSchema": schema, "disposition": disposition,
            "requestBody": operation.get("requestBody"), "responses": operation.get("responses", {})}


def operations(service: Any, group_version: str) -> dict:
    evidence = openapi(service, group_version)
    contracts = []
    for row in evidence["operations"]:
        try:
            contract = operation_contract(evidence["openapi"], row["operationId"])
        except OperationError as exc:
            contracts.append({**row, "registration": "unsupported_schema", "reason": exc.code})
            continue
        if not reviewed_read(group_version, row["operationId"]):
            contract["disposition"] = "unreviewed_group"
        contract["readTool"] = "plugin.operation.read" if contract["disposition"] == "read" else None
        contracts.append({**row, **contract, "registration": "implemented" if contract["readTool"] else "not_implemented",
                          "arguments": {"groupVersion": group_version, "operationId": row["operationId"], "schemaHash": evidence["sha256"]}})
    return {key: evidence[key] for key in ("cluster", "groupVersion", "source", "sha256", "auditRef")} | {"operations": contracts}


def sanitize_resource(value: Any) -> Any:
    # Return resource evidence, never raw CLI envelopes or historical manifests.
    denied = {"data", "stringdata", "password", "token", "privatekey", "clientsecret", "secretaccesskey",
              "authorization", "kubeconfig", "snippets", "textsnippets", "rawevents"}
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if key.lower() in denied else sanitize_resource(item))
                for key, item in value.items() if key not in {"managedFields", "annotations"}}
    if isinstance(value, list):
        return [sanitize_resource(item) for item in value]
    return value


STORAGE_PROJECTION = json.loads(Path(__file__).with_name("storage-read-projection.json").read_text())


def project_storage_resource(value):
    """Preserve reviewed resource evidence without open-ended configuration data."""
    def fields(item, rule):
        if isinstance(item, list):
            return [fields(child, rule) for child in item]
        if isinstance(item, dict):
            if rule is True:
                return "[REDACTED]"
            return {key: fields(child, rule[key]) for key, child in item.items() if key in rule}
        return item if rule is True or item is None else None

    if isinstance(value, list):
        return [project_storage_resource(item) for item in value]
    if not isinstance(value, dict):
        raise OperationError("PLUGIN_RESPONSE_CONTRACT_FAILED", "Expected a resource object")
    if "object" in value and "type" in value:
        return {"type": value["type"], "object": project_storage_resource(value["object"])}
    result = {key: value[key] for key in ("apiVersion", "kind") if key in value}
    if "metadata" in value:
        result["metadata"] = fields(value["metadata"], STORAGE_PROJECTION["metadata"])
    if "items" in value:
        result["items"] = [project_storage_resource(item) for item in value["items"]]
    elif value.get("kind") in STORAGE_PROJECTION["specByKind"]:
        if "spec" in value:
            result["spec"] = fields(value["spec"], STORAGE_PROJECTION["specByKind"][value["kind"]])
        if "status" in value:
            result["status"] = fields(value["status"], STORAGE_PROJECTION["status"])
    elif set(value) - {"apiVersion", "kind", "metadata"}:
        raise OperationError("PLUGIN_RESPONSE_CONTRACT_FAILED", "Unreviewed storage resource kind")
    return result


def validate_response_body(document: dict, contract: dict, body: Any, watch: bool) -> dict:
    """Validate decoded JSON only; kubectl does not preserve HTTP headers/status."""
    from jsonschema import Draft4Validator, validators
    from referencing import Registry
    from referencing.exceptions import Unresolvable
    from jsonschema.exceptions import SchemaError, UnknownType

    if not str(document.get("openapi", "3.0.0")).startswith("3.0."):
        raise OperationError("PLUGIN_SCHEMA_UNSUPPORTED", "Response validation currently requires OpenAPI 3.0")
    response = local_ref(document, contract["responses"].get("200", {}))
    schema = response.get("content", {}).get("application/json", {}).get("schema")
    if not isinstance(schema, dict) or not schema:
        raise OperationError("PLUGIN_SCHEMA_UNSUPPORTED", "Native JSON response schema is missing")

    def nullable_type(validator, types, instance, subschema):
        if instance is None and subschema.get("nullable") is True:
            return
        yield from Draft4Validator.VALIDATORS["type"](validator, types, instance, subschema)

    # OpenAPI 3.0 uses boolean exclusiveMinimum/Maximum and nullable, unlike
    # JSON Schema 2020-12. An explicit empty registry forbids remote retrieval.
    validator_class = validators.extend(Draft4Validator, {"type": nullable_type})
    validator = validator_class(document, registry=Registry())

    def check(value, target):
        try:
            if next(validator.evolve(schema=target).iter_errors(value), None) is not None:
                raise OperationError("PLUGIN_RESPONSE_CONTRACT_FAILED", "Native response body violates its schema")
        except (Unresolvable, SchemaError, UnknownType, RecursionError) as exc:
            raise OperationError("PLUGIN_SCHEMA_UNSUPPORTED", "Response schema cannot be resolved locally") from exc

    if not watch:
        check(body, schema)
        return {"mode": "native_json_body", "validatedObjects": 1, "httpConformance": "not_run"}

    resource_list = local_ref(document, schema)
    items = resource_list.get("properties", {}).get("items", {}).get("items")
    if not isinstance(items, dict):
        raise OperationError("PLUGIN_SCHEMA_UNSUPPORTED", "Watch requires a native list item schema")
    checked = 0
    for event in body:
        if not isinstance(event, dict) or event.get("type") not in {"ADDED", "MODIFIED", "DELETED", "BOOKMARK", "ERROR"} or not isinstance(event.get("object"), dict):
            raise OperationError("PLUGIN_RESPONSE_CONTRACT_FAILED", "Invalid native watch event")
        if event["type"] == "ERROR":
            raise OperationError("PLUGIN_WATCH_FAILED", "Native watch emitted an error; refresh discovery and resourceVersion")
        if event["type"] == "BOOKMARK":
            metadata = event["object"].get("metadata")
            resource_version = metadata.get("resourceVersion") if isinstance(metadata, dict) else None
            if not isinstance(resource_version, str) or not resource_version:
                raise OperationError("PLUGIN_RESPONSE_CONTRACT_FAILED", "Watch bookmark has no resourceVersion")
        else:
            check(event["object"], items)
            checked += 1
    return {"mode": "watch_envelope_and_observed_objects", "validatedObjects": checked, "httpConformance": "not_run"}


def prepare_read(service: Any, group_version: str, operation_id: str, schema_hash: str, parameters: dict | None = None) -> dict:
    from jsonschema import Draft202012Validator
    from urllib.parse import quote, urlencode

    if not isinstance(group_version, str) or not isinstance(operation_id, str) or not reviewed_read(group_version, operation_id):
        raise OperationError("PLUGIN_GROUP_NOT_REVIEWED", "Resource reads are not enabled for this group/version")
    if not isinstance(operation_id, str) or not operation_id or not isinstance(schema_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", schema_hash):
        raise OperationError("INVALID_ARGUMENT", "operationId and schemaHash from plugin.operations.list are required")
    evidence = openapi(service, group_version)
    if schema_hash != evidence["sha256"]:
        raise OperationError("PLUGIN_SCHEMA_CHANGED", "Refresh operation discovery before calling")
    contract = operation_contract(evidence["openapi"], operation_id)
    if contract["disposition"] != "read":
        raise OperationError("PLUGIN_OPERATION_NOT_READ_ONLY", "Mutation uses its declared GitOps or controller authority")
    parameters = {} if parameters is None else parameters
    if list(Draft202012Validator(contract["inputSchema"]).iter_errors(parameters)):
        raise OperationError("INVALID_ARGUMENT", "Parameters do not match the discovered operation inputSchema")
    path = contract["pathTemplate"]
    for name, value in parameters.get("path", {}).items():
        if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", value) or ".." in value:
            raise OperationError("INVALID_ARGUMENT", "Path values must be Kubernetes resource names")
        path = path.replace("{" + name + "}", quote(value, safe=""))
    if "{" in path or "}" in path or not path.startswith("/" + group_version + "/"):
        raise OperationError("INVALID_ARGUMENT", "Native path could not be safely bound")
    query = dict(parameters.get("query", {}))
    supported = contract["inputSchema"]["properties"]["query"]["properties"]
    watch = query.get("watch", False)
    if "timeoutSeconds" in supported:
        query.setdefault("timeoutSeconds", 10)
        if not 1 <= query["timeoutSeconds"] <= 20:
            raise OperationError("INVALID_ARGUMENT", "timeoutSeconds must be between 1 and 20")
    if "limit" in supported and not watch:
        query.setdefault("limit", 100)
        if not 1 <= query["limit"] <= 500:
            raise OperationError("INVALID_ARGUMENT", "limit must be between 1 and 500; use the native continue token")
    if watch and "timeoutSeconds" not in supported:
        raise OperationError("PLUGIN_SCHEMA_UNSUPPORTED", "Watch requires a bounded native timeout")
    suffix = urlencode({key: str(value).lower() if isinstance(value, bool) else value for key, value in sorted(query.items())})
    return {"evidence": evidence, "contract": contract, "path": path + ("?" + suffix if suffix else ""),
            "parameters": {"path": parameters.get("path", {}), "query": query}, "watch": watch}


def read_operation(service: Any, group_version: str, operation_id: str, schema_hash: str, parameters: dict | None = None) -> dict:
    prepared = prepare_read(service, group_version, operation_id, schema_hash, parameters)
    evidence, contract, watch = prepared["evidence"], prepared["contract"], prepared["watch"]
    result = service._kubectl(["get", "--raw", prepared["path"]])
    if result.get("exitCode") != 0 or result.get("truncated"):
        raise OperationError("PLUGIN_READ_FAILED", "Native resource read failed or was truncated; no partial response is returned")
    try:
        body = [json.loads(line) for line in result["stdout"].splitlines() if line.strip()] if watch else json.loads(result["stdout"])
    except (ValueError, KeyError) as exc:
        raise OperationError("PLUGIN_READ_FAILED", "Invalid native JSON response") from exc
    validation = validate_response_body(evidence["openapi"], contract, body, watch)
    return {"cluster": evidence["cluster"], "operationId": operation_id, "schemaHash": schema_hash,
            "auditRef": result.get("auditRef"), "watch": watch, "bodyValidation": validation, "result": project_storage_resource(body) if group_version in STORAGE_PROJECTION["groups"] else sanitize_resource(body)}
