#!/usr/bin/env python3
"""Schema-derived, read-only tenant MCP contract checks for repeatable CI."""

from __future__ import annotations

import argparse
import os
import sys

from jsonschema import Draft202012Validator

from run import AcceptanceError, MCP, require


# These operations form the provider lifecycle contract. A missing tool is a
# deployment regression even when the backend still returns HTTP 200.
REQUIRED_TOOLS = {
    "tenant_capabilities_list",
    "service_catalog_list",
    "service_instance_plan",
    "service_instance_create",
    "service_instance_get",
    "service_instance_list",
    "service_instance_suspend",
    "service_instance_resume",
    "service_instance_delete",
    "tenant_human_access_issue",
    "harbor_project_get",
    "harbor_repository_list",
}

# Only these methods receive generated invalid input. They cannot change
# infrastructure or provider state, even if the server ignores inputSchema.
READ_ONLY_GENERATION = {
    "service_instance_get",
    "service_instance_plan",
    "harbor_project_get",
    "harbor_repository_list",
}


def logical_name(published_name):
    prefix = "tenant-api-backend_"
    return published_name[len(prefix):] if published_name.startswith(prefix) else published_name


def inspect_tools(tools):
    require(isinstance(tools, list) and tools, "tenant MCP published no tools")
    by_name = {}
    for tool in tools:
        require(isinstance(tool, dict) and isinstance(tool.get("name"), str), "invalid MCP tool descriptor")
        name = logical_name(tool["name"])
        require(name not in by_name, f"duplicate MCP tool {name}")
        schema = tool.get("inputSchema")
        require(isinstance(schema, dict), f"{name} has no input schema")
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:
            raise AcceptanceError(f"{name} has an invalid input schema: {type(exc).__name__}") from None
        require(schema.get("type") == "object", f"{name} input schema is not an object")
        properties = schema.get("properties") or {}
        require(isinstance(properties, dict), f"{name} properties are invalid")
        require(set(schema.get("required") or []) <= set(properties), f"{name} requires undefined fields")
        by_name[name] = (tool["name"], schema)
    missing = sorted(REQUIRED_TOOLS - by_name.keys())
    require(not missing, "tenant provider tools missing: " + ", ".join(missing))
    return by_name


def invalid_cases(schema):
    """Generate deterministic boundary cases from each required JSON field."""
    required = schema.get("required") or []
    properties = schema.get("properties") or {}
    baseline = {}
    for name in required:
        kind = properties[name].get("type")
        baseline[name] = {"string": "contract-probe", "integer": 1, "number": 1, "boolean": False, "array": [], "object": {}}.get(kind, None)
    validator = Draft202012Validator(schema)
    cases = []
    for name in required:
        missing = {key: value for key, value in baseline.items() if key != name}
        wrong_type = {**baseline, name: ["wrong-type"]}
        for label, arguments in ((f"missing {name}", missing), (f"wrong type {name}", wrong_type)):
            if not validator.is_valid(arguments):
                cases.append((label, arguments))
    return cases


def generated_boundary_checks(mcp, tools):
    count = 0
    for name in sorted(READ_ONLY_GENERATION):
        published, schema = tools[name]
        cases = invalid_cases(schema)
        require(cases, f"{name} declares no testable required input")
        for label, arguments in cases:
            print(f"CHECK {name}: {label}", flush=True)
            try:
                result = mcp.call("tools/call", {"name": published, "arguments": arguments}, timeout=10)
            except AcceptanceError as exc:
                rejected = "returned a JSON-RPC error" in str(exc) or any(
                    f"returned HTTP {status}" in str(exc) for status in (400, 403, 422)
                )
                require(rejected, f"{name} {label}: {exc}")
            else:
                require(isinstance(result, dict) and result.get("isError") is True,
                        f"{name} accepted schema-invalid input ({label})")
            count += 1
    return count


def run_contract(url, token, tenant, publication_only=False):
    mcp = MCP(url, token, tenant)
    listing = mcp.call("tools/list", {})
    tools = inspect_tools(listing.get("tools") if isinstance(listing, dict) else None)
    print(f"PASS {len(tools)} published MCP input schemas; {len(REQUIRED_TOOLS)} required provider tools")
    if publication_only:
        result = mcp.tool("tenant_capabilities_list", {})
        require(isinstance(result, dict), "ToolHive capability result is invalid")
        print("PASS ToolHive tenant publication")
        return
    for name in ("tenant_capabilities_list", "service_catalog_list", "service_instance_list"):
        result = mcp.tool(name, {})
        require(isinstance(result, dict), f"{name} returned no object")
        print(f"PASS {name}")
    count = generated_boundary_checks(mcp, tools)
    print(f"PASS {count} generated read-only invalid-input cases")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp-url", default="https://tools.service.re8ch.com/tenant-native/mcp")
    parser.add_argument("--publication-only", action="store_true")
    args = parser.parse_args()
    token = os.getenv("TENANT_ACCESS_TOKEN", "")
    tenant = os.getenv("TENANT_REF", "")
    try:
        require(bool(token and tenant), "TENANT_ACCESS_TOKEN and TENANT_REF are required")
        run_contract(args.mcp_url, token, tenant, args.publication_only)
    except AcceptanceError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
