"""Bounded real HTTP response checks; never export response payloads or credentials."""
import copy
import threading
from importlib.metadata import version

from runtime import OperationError
from plugin_capabilities import prepare_read

SLOT = threading.BoundedSemaphore(1)

CHECK_NAMES = ("status_code_conformance", "content_type_conformance",
               "response_headers_conformance", "response_schema_conformance")


def require_local_refs(value):
    if isinstance(value, dict):
        if "$ref" in value and (not isinstance(value["$ref"], str) or not value["$ref"].startswith("#/")):
            raise OperationError("PLUGIN_SCHEMA_UNSUPPORTED", "Only document-local schema references are accepted")
        for child in value.values():
            require_local_refs(child)
    elif isinstance(value, list):
        for child in value:
            require_local_refs(child)


def check_response(document, contract, parameters, response):
    import requests
    import schemathesis
    from schemathesis.specs.openapi import checks
    from schemathesis.core.transport import Response
    from schemathesis.core.failures import FailureGroup

    require_local_refs(document)
    # Ignore advertised servers: only the bound server-side transport can send.
    document = copy.deepcopy(document)
    document.pop("servers", None)
    schema = schemathesis.openapi.from_dict(document)
    case = schema[contract["pathTemplate"]]["GET"].Case(
        path_parameters=parameters["path"], query=parameters["query"])
    actual = Response(status_code=response["status"],
        headers={key: value if isinstance(value, list) else [value] for key, value in response["headers"].items()},
        content=response["body"], request=requests.Request("GET", "http://localhost/native-read").prepare(),
        elapsed=response.get("elapsed", 0), verify=True, encoding="utf-8",
        http_version=response.get("httpVersion", "1.1"))
    results = []
    for name in CHECK_NAMES:
        try:
            case.validate_response(actual, checks=[getattr(checks, name)])
        except FailureGroup:
            # Schemathesis exceptions can embed full bodies/curl reproductions.
            results.append({"check": name, "status": "failed"})
        except Exception:
            results.append({"check": name, "status": "error"})
        else:
            results.append({"check": name, "status": "passed"})
    # A documented authentication/error response is not successful read coverage.
    success = actual.status_code == 200 and all(row["status"] == "passed" for row in results)
    return {"engine": "schemathesis", "version": version("schemathesis"),
            "scope": "one_finite_GET_response", "networkRequests": 1,
            "statusCode": actual.status_code, "checks": results,
            "httpConformance": "not_run" if any(row["status"] == "error" for row in results) else "passed" if success else "failed",
            "semanticConformance": "not_run", "bodyReturned": False,
            "coverage": "single supplied case; no fuzzing, mutations, watch, or whole-operation completeness claim"}


def conformance(service, group_version, operation_id, schema_hash, parameters=None):
    if not SLOT.acquire(blocking=False):
        raise OperationError("PLUGIN_HTTP_BUSY", "Another HTTP acceptance case is running", retryable=True)
    try:
        return _conformance(service, group_version, operation_id, schema_hash, parameters)
    finally:
        SLOT.release()


def _conformance(service, group_version, operation_id, schema_hash, parameters=None):
    prepared = prepare_read(service, group_version, operation_id, schema_hash, parameters)
    if prepared["watch"]:
        raise OperationError("PLUGIN_HTTP_STREAM_UNSUPPORTED", "Finite HTTP acceptance excludes watch streams")
    document = prepared["evidence"]["openapi"]
    require_local_refs(document)
    result = service._plugin_http_get(prepared["path"])
    report = check_response(document, prepared["contract"], prepared["parameters"], result)
    return {"cluster": prepared["evidence"]["cluster"], "operationId": operation_id,
            "schemaHash": schema_hash, "auditRef": result["auditRef"], **report}
