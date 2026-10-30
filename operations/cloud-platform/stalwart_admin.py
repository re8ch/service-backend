"""Bounded Stalwart management through the same JMAP API as its official CLI.

The caller cannot supply endpoints, credentials, native methods or arbitrary
patches. Apply consumes a plan installed by the reviewed delivery pipeline.
Native management capability discovery is required; older servers fail closed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from runtime import OperationError

CAPABILITY = "urn:stalwart:jmap"
ID_PATTERN = r"^[A-Za-z0-9_-]{1,128}$"
HASH_PATTERN = r"^[0-9a-f]{64}$"
QUOTAS = ("maxDiskQuota", "maxEmails", "maxMailboxes", "maxCalendars",
          "maxCalendarEvents", "maxFiles", "maxFolders")
FIELDS = {
    "Account": ("id", "@type", "name", "domainId", "emailAddress", "description", "createdAt", "quotas", "usedDiskQuota"),
    "Domain": ("id", "name", "enable", "description", "createdAt"),
}
MAX_RESPONSE = 1 << 20


def tool(name, description, properties, required, *, read_only):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "additionalProperties": False,
                            "properties": properties, "required": required},
            "annotations": {"readOnlyHint": read_only, "destructiveHint": not read_only,
                            "openWorldHint": False}}


TARGET = {"object": {"type": "string", "enum": list(FIELDS)},
          "id": {"type": "string", "pattern": ID_PATTERN}}
PATCH = {"type": "object", "additionalProperties": False, "minProperties": 1,
         "properties": {
             "description": {"type": ["string", "null"], "maxLength": 512},
             "quotas": {"type": "object", "additionalProperties": False, "minProperties": 1,
                        "properties": {key: {"type": "integer", "minimum": 1,
                                             "maximum": (1 << 40) if key == "maxDiskQuota" else 1_000_000_000}
                                       for key in QUOTAS}}}}
TOOLS = [
    tool("stalwart.admin.read", "Read bounded account or domain metadata and quota summaries. Platform administrator only; credentials and mail content are never returned.",
         {**TARGET, "position": {"type": "integer", "minimum": 0, "maximum": 1_000_000},
          "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 25}}, ["object"], read_only=True),
    tool("stalwart.admin.plan", "Preview an account description/quota or domain description change. Returns an exact plan for signed GitOps review; preview grants no apply authority.",
         {**TARGET, "patch": PATCH}, ["object", "id", "patch"], read_only=True),
    tool("stalwart.admin.apply", "Apply a hash-selected plan installed by reviewed GitOps delivery, with native state preconditions and readback. Cannot accept or approve a plan from tool arguments.",
         {"planHash": {"type": "string", "pattern": HASH_PATTERN}}, ["planHash"], read_only=False),
]
BY_NAME = {item["name"]: item for item in TOOLS}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, endpoint, token, *, opener=None):
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise OperationError("STALWART_CONFIGURATION_INVALID", "A fixed HTTPS provider origin is required")
        if not token or len(token) > 16384 or any(c.isspace() for c in token):
            raise OperationError("STALWART_CREDENTIAL_UNAVAILABLE", "Dedicated management credential is unavailable")
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.api_url = None

    @classmethod
    def configured(cls):
        endpoint = os.getenv("STALWART_ADMIN_ORIGIN", "")
        token_file = os.getenv("STALWART_ADMIN_TOKEN_FILE", "")
        if not endpoint or not token_file:
            raise OperationError("STALWART_NOT_CONFIGURED", "Management provider and dedicated token reference are required")
        try:
            with open(token_file, "rb") as handle:
                raw = handle.read(16385)
            if len(raw) > 16384:
                raise ValueError()
            token = raw.decode().strip()
        except (OSError, ValueError, UnicodeError):
            raise OperationError("STALWART_CREDENTIAL_UNAVAILABLE", "Dedicated management credential is unavailable") from None
        return cls(endpoint, token)

    def authorization_header(self):
        return "Bearer " + self.token

    def request(self, url, payload=None):
        body = canonical(payload) if payload is not None else None
        req = urllib.request.Request(url, data=body, headers={"Authorization": self.authorization_header(),
                                     "Accept": "application/json", "Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=15) as response:
                if response.status != 200:
                    raise OperationError("STALWART_PROVIDER_FAILED", "Management provider returned an unexpected status")
                raw = response.read(MAX_RESPONSE + 1)
        except (urllib.error.URLError, TimeoutError, OSError):
            # Provider error text can echo credentials or request data.
            raise OperationError("STALWART_PROVIDER_FAILED", "Management request failed; no automatic replay") from None
        if len(raw) > MAX_RESPONSE:
            raise OperationError("STALWART_RESPONSE_LIMIT", "Management response exceeded the byte limit")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise OperationError("STALWART_RESPONSE_INVALID", "Management response was not JSON") from None
        if not isinstance(result, dict):
            raise OperationError("STALWART_RESPONSE_INVALID", "Management response must be an object")
        return result

    def discover(self):
        session = self.request(self.endpoint + "/jmap/session")
        capabilities = session.get("capabilities")
        if not isinstance(capabilities, dict) or CAPABILITY not in capabilities:
            raise OperationError("STALWART_UNSUPPORTED", "Server does not advertise the required management capability")
        api_url = session.get("apiUrl")
        if not isinstance(api_url, str):
            raise OperationError("STALWART_RESPONSE_INVALID", "Management API URL missing")
        parsed = urllib.parse.urlsplit(api_url)
        origin = urllib.parse.urlsplit(self.endpoint)
        if parsed.scheme != origin.scheme or parsed.netloc != origin.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("/jmap", "/jmap/"):
            raise OperationError("STALWART_ENDPOINT_REJECTED", "Management API URL is outside the reviewed origin/path")
        self.api_url = api_url

    def method(self, object_type, operation, arguments):
        if object_type not in FIELDS or operation not in {"get", "query", "set"}:
            raise OperationError("INVALID_ARGUMENT", "Native operation is not allowed")
        if self.api_url is None:
            self.discover()
        method = "x:" + object_type + "/" + operation
        result = self.request(self.api_url, {"using": ["urn:ietf:params:jmap:core", CAPABILITY],
                              "methodCalls": [[method, arguments, "admin"]]})
        responses = result.get("methodResponses")
        if not isinstance(responses, list) or len(responses) != 1:
            raise OperationError("STALWART_RESPONSE_INVALID", "Unexpected management method response")
        response = responses[0]
        if not isinstance(response, list) or len(response) != 3 or response[2] != "admin" or not isinstance(response[1], dict):
            raise OperationError("STALWART_RESPONSE_INVALID", "Invalid management method response")
        if response[0] == "error":
            code = response[1].get("type")
            if code == "stateMismatch":
                raise OperationError("STALWART_PLAN_STALE", "Provider state changed; create and review a new plan")
            if code == "unknownMethod":
                raise OperationError("STALWART_UNSUPPORTED", "Management method is unsupported by this server")
            raise OperationError("STALWART_METHOD_FAILED", "Management method rejected the request")
        if response[0] != method:
            raise OperationError("STALWART_RESPONSE_INVALID", "Unexpected management method")
        return response[1]

    def get(self, object_type, ids):
        result = self.method(object_type, "get", {"ids": ids, "properties": list(FIELDS[object_type])})
        values = result.get("list")
        state = result.get("state")
        if not isinstance(values, list) or len(values) > len(ids) or not isinstance(state, str) or not state or len(state) > 512:
            raise OperationError("STALWART_RESPONSE_INVALID", "Invalid management object list/state")
        seen = set()
        for value in values:
            if not isinstance(value, dict) or value.get("id") not in ids or value["id"] in seen:
                raise OperationError("STALWART_RESPONSE_INVALID", "Unexpected management object")
            seen.add(value["id"])
        return values, state


def project(object_type, value):
    result = {}
    for key in FIELDS[object_type]:
        item = value.get(key)
        if key == "quotas":
            if isinstance(item, dict):
                result[key] = {k: v for k, v in item.items() if k in QUOTAS and type(v) is int and 0 <= v <= (1 << 53)}
        elif isinstance(item, str):
            result[key] = item[:1024]
        elif item is None or type(item) in (bool, int):
            result[key] = item
    return result


def read(client, args):
    object_type = args["object"]
    if args.get("id"):
        values, state = client.get(object_type, [args["id"]])
        return {"object": object_type, "items": [project(object_type, v) for v in values], "state": state}
    position, limit = args.get("position", 0), args.get("limit", 25)
    result = client.method(object_type, "query", {"position": position, "limit": limit, "calculateTotal": False})
    ids = result.get("ids")
    if not isinstance(ids, list) or len(ids) > limit or any(not isinstance(v, str) or not re.fullmatch(ID_PATTERN, v) for v in ids) or len(set(ids)) != len(ids):
        raise OperationError("STALWART_RESPONSE_INVALID", "Invalid management query IDs")
    values, state = client.get(object_type, ids) if ids else ([], None)
    return {"object": object_type, "items": [project(object_type, v) for v in values],
            "state": state, "position": position, "nextPosition": position + len(ids) if len(ids) == limit else None,
            "pageMayHaveMore": len(ids) == limit}


def checked_patch(object_type, patch):
    if object_type == "Domain" and "quotas" in patch:
        raise OperationError("INVALID_ARGUMENT", "Domain quota changes are not supported")
    return patch


def one(client, object_type, object_id):
    values, state = client.get(object_type, [object_id])
    if len(values) != 1:
        raise OperationError("STALWART_OBJECT_NOT_FOUND", "Management object was not found")
    return values[0], state


def desired_patch(current, patch):
    # Patch individual quota entries; never replace the whole quotas map or
    # accidentally remove limits the user did not request.
    native = {k: v for k, v in patch.items() if k != "quotas"}
    for key, value in patch.get("quotas", {}).items():
        if key == "maxDiskQuota":
            used = current.get("usedDiskQuota")
            if type(used) is not int or used < 0:
                raise OperationError("STALWART_RESPONSE_INVALID", "Current disk usage is required for a quota change")
            if value < used:
                raise OperationError("STALWART_QUOTA_BELOW_USAGE", "Requested disk quota is below current usage")
        native["quotas/" + key] = value
    return native


def plan(client, args, subject):
    patch = checked_patch(args["object"], args["patch"])
    current, state = one(client, args["object"], args["id"])
    native = desired_patch(current, patch)
    content = {"version": 1, "origin": client.endpoint, "subject": subject,
               "object": args["object"], "id": args["id"], "ifInState": state,
               "patch": patch, "nativePatch": native}
    return {"decision": "awaiting_gitops_review", "planHash": digest(content), "plan": content,
            "before": project(args["object"], current),
            "nativeValidation": "deferred_to_provider_on_apply",
            "approvalRequired": "signed_pr_and_reviewed_delivery", "applied": False}


def approved_plan(plan_hash, subject, origin, *, directory=None, now=None):
    if not isinstance(plan_hash, str) or not re.fullmatch(HASH_PATTERN, plan_hash):
        raise OperationError("INVALID_ARGUMENT", "A canonical plan hash is required")
    directory = directory or os.getenv("STALWART_APPROVED_PLANS_DIR", "")
    if not directory:
        raise OperationError("STALWART_APPROVAL_REQUIRED", "No reviewed management plans are installed")
    try:
        # Kubernetes projected ConfigMap files are symlinks. The directory is a
        # trusted read-only deployment mount; only a strict hash becomes a path.
        with open(Path(directory) / (plan_hash + ".json"), "rb") as handle:
            raw = handle.read(65537)
        if len(raw) > 65536:
            raise ValueError()
        record = json.loads(raw)
        content = record["plan"]
        expiry = record["expiresAt"]
        revision = record["approvedRevision"]
        if not isinstance(content, dict) or digest(content) != plan_hash or type(expiry) is not int or not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError()
        if expiry <= (time.time() if now is None else now) or content.get("subject") != subject or content.get("origin") != origin:
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise OperationError("STALWART_APPROVAL_REQUIRED", "No matching unexpired reviewed plan is installed") from None
    from jsonschema import Draft202012Validator
    expected = {"version", "origin", "subject", "object", "id", "ifInState", "patch", "nativePatch"}
    if set(content) != expected or type(content["version"]) is not int or content["version"] != 1 or not isinstance(content["ifInState"], str) or not content["ifInState"] or len(content["ifInState"]) > 512:
        raise OperationError("STALWART_APPROVAL_INVALID", "Reviewed plan has an invalid shape")
    args = {k: content[k] for k in ("object", "id", "patch")}
    if list(Draft202012Validator(BY_NAME["stalwart.admin.plan"]["inputSchema"]).iter_errors(args)):
        raise OperationError("STALWART_APPROVAL_INVALID", "Reviewed plan has unsupported changes")
    checked_patch(content["object"], content["patch"])
    return content, revision


def matches(current, patch):
    return all(current.get(k) == v for k, v in patch.items() if k != "quotas") and all(
        isinstance(current.get("quotas"), dict) and current["quotas"].get(k) == v for k, v in patch.get("quotas", {}).items())


def apply(client, args, subject):
    content, revision = approved_plan(args["planHash"], subject, client.endpoint)
    current, state = one(client, content["object"], content["id"])
    native = desired_patch(current, content["patch"])
    if native != content["nativePatch"]:
        raise OperationError("STALWART_APPROVAL_INVALID", "Reviewed native patch does not match bounded changes")
    result = {"planHash": args["planHash"], "approvedRevision": revision,
              "object": content["object"], "id": content["id"]}
    if matches(current, content["patch"]):
        return {**result, "status": "already_satisfied", "applied": False, "verified": True}
    if state != content["ifInState"]:
        raise OperationError("STALWART_PLAN_STALE", "Provider state changed; create and review a new plan")
    try:
        response = client.method(content["object"], "set", {"ifInState": state, "update": {content["id"]: native}})
        if not isinstance(response.get("updated"), dict) or content["id"] not in response["updated"] or response.get("notUpdated"):
            raise OperationError("STALWART_UPDATE_REJECTED", "Provider did not confirm the approved update")
        current, _ = one(client, content["object"], content["id"])
    except OperationError as error:
        if error.code in {"STALWART_PLAN_STALE", "STALWART_UPDATE_REJECTED", "STALWART_METHOD_FAILED", "STALWART_UNSUPPORTED"}:
            raise
        return {**result, "status": "outcome_unknown", "applied": None, "verified": False,
                "recovery": "Read the target before retrying; this invocation did not replay the write"}
    if not matches(current, content["patch"]):
        return {**result, "status": "readback_mismatch", "applied": True, "verified": False}
    return {**result, "status": "applied", "applied": True, "verified": True,
            "after": project(content["object"], current)}


def call(name, args, subject, *, client=None):
    from jsonschema import Draft202012Validator
    definition = BY_NAME.get(name)
    if definition is None or list(Draft202012Validator(definition["inputSchema"]).iter_errors(args)):
        raise OperationError("INVALID_ARGUMENT", "Arguments do not match the bounded Stalwart schema")
    if not subject:
        raise OperationError("AUTHORIZATION_DENIED", "Authenticated management subject is required")
    operation_id = "op_" + uuid.uuid4().hex
    event = {"event": "stalwart.management", "operationId": operation_id, "tool": name,
             "subjectRef": hashlib.sha256(subject.encode()).hexdigest()}
    try:
        client = client or Client.configured()
        if name == "stalwart.admin.read":
            result = read(client, args)
        elif name == "stalwart.admin.plan":
            result = plan(client, args, subject)
        else:
            result = apply(client, args, subject)
        event["outcome"] = result.get("status", result.get("decision", "read"))
        if "planHash" in result:
            event["planHash"] = result["planHash"]
        return {**result, "operationId": operation_id}
    except OperationError as error:
        event.update(outcome="error", errorCode=error.code)
        raise
    finally:
        # No arguments, object contents, provider response or token in telemetry.
        logging.getLogger(__name__).warning(json.dumps(event, sort_keys=True))
