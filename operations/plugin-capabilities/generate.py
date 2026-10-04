#!/usr/bin/env python3
"""Compile captured native read contracts into reviewed MCP tools and registry metadata."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "operations/cloud-platform"))
from mihomos_capabilities import TOOLS as MIHOMOS_TOOLS
from anatomy_diff_operation import TOOL as ANATOMY_DIFF_TOOL, DELETE_TOOL as ANATOMY_DIFF_DELETE_TOOL
from plugin_capabilities import STORAGE_PROJECTION
from plugin_capabilities import GROUP_PLUGINS, RESOURCE_PLUGINS, ADVFAB_KINDS, reviewed_read, operation_contract, operation_disposition


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def capability_inventory(matrix, tools):
    """Publish inventory separately from callable definitions; missing adapters stay visible."""
    deployed = deployed_tools([*tools, *MIHOMOS_TOOLS, ANATOMY_DIFF_TOOL, ANATOMY_DIFF_DELETE_TOOL])
    by_operation = {(tool["groupVersion"], tool["operationId"]): tool for tool in tools}
    rows = []
    for source in matrix["sources"]:
        group = source["groupVersion"].split("/")[1]
        for row in source["operations"]:
            plugin = GROUP_PLUGINS.get(group, RESOURCE_PLUGINS.get(row["kind"], "unclassified"))
            if group == "networking.re8ch.com" and row["kind"] in ADVFAB_KINDS:
                plugin = "advfab-legacy"
            tool = by_operation.get((source["groupVersion"], row["operationId"]))
            rows.append({"plugin": plugin, "groupVersion": source["groupVersion"],
                "method": row["method"].upper(), "path": row["path"], "operationId": row["operationId"],
                "registration": ("deployed" if tool["name"] in deployed else "implemented_not_deployed") if tool else "not_implemented",
                "disposition": "resource_read_review_required" if row["method"] == "get" and not reviewed_read(source["groupVersion"], row["operationId"]) else operation_disposition(source["groupVersion"], row["kind"], row["method"], row["path"]),
                "schemaProvenance": "native_openapi", "httpConformance": "not_run", "semanticConformance": "not_run",
                **({"tool": tool["name"]} if tool else {})})
    controller = json.loads((ROOT / "operations/plugin-capabilities/mihomos-native-surfaces.json").read_text())
    for row in controller["operations"]:
        rows.append({"plugin": controller["plugin"], "groupVersion": "controller/" + controller["version"],
            **{key: row[key] for key in ("method", "path", "registration", "disposition", "httpConformance", "semanticConformance")},
            "schemaProvenance": row.get("schemaProvenance", "source_route_only"),
            **{key: row[key] for key in ("operationId", "tool") if key in row}})
    for row in rows:
        if row.get("tool") in deployed:
            row["registration"] = "deployed"
    return {"version": 1, "cluster": matrix["cluster"],
        "scope": matrix["scope"] + "; includes inventoried Mihomo routes, excludes conditional controller surfaces",
        "operations": sorted(rows, key=lambda row: (row["plugin"], row["groupVersion"], row["path"], row["method"]))}


def deployed_tools(tools):
    """Historical registration evidence applies only to matching input contracts."""
    path = ROOT / "operations/plugin-capabilities/deployment-evidence.json"
    if not path.exists():
        return set()
    evidence = json.loads(path.read_text())
    if evidence.get("version") != 1 or evidence.get("cluster") != "re8ch-k3s":
        raise ValueError("Unsupported deployment evidence")
    hashes = {row["name"]: row["inputSchemaSha256"] for row in evidence["tools"]}
    return {tool["name"] for tool in tools
            if hashes.get(tool["name"]) == hashlib.sha256(canonical(tool["inputSchema"])).hexdigest()}


def generate():
    matrix = json.loads((ROOT / "operations/plugin-capabilities/native-operation-matrix.json").read_text())
    output = ROOT / "operations/cloud-platform/plugin-operation-tools.json"
    previous_names = {item["name"] for item in json.loads(output.read_text())["tools"]} if output.exists() else set()
    tools = {}
    for source in matrix["sources"]:
        doc = json.loads((ROOT / "operations/plugin-capabilities" / source["schemaFile"]).read_text())
        digest = hashlib.sha256(canonical(doc)).hexdigest()
        group = source["groupVersion"].split("/")[1]
        for row in source["operations"]:
            if row["method"] != "get" or not reviewed_read(source["groupVersion"], row["operationId"]):
                continue
            contract = operation_contract(doc, row["operationId"])
            plugin = GROUP_PLUGINS.get(group, RESOURCE_PLUGINS.get(row["kind"], "unclassified"))
            if group == "networking.re8ch.com" and row["kind"] in ADVFAB_KINDS:
                plugin = "advfab-legacy"
            identity = source["groupVersion"] + ":" + row["operationId"]
            suffix = hashlib.sha256(identity.encode()).hexdigest()[:12]
            stem = f"{plugin}.{row['kind'].lower()}.{row['action']}"[:50].rstrip(".")
            name = stem + "." + suffix
            if name in tools:
                raise ValueError("Generated tool collision: " + name)
            description = (f"Read {row['kind']} through native {source['groupVersion']}: {row['operationId']}. "
                           "Platform admin only; bounded lists/watch; credentials and historical manifests are omitted.")
            if source["groupVersion"] in STORAGE_PROJECTION["groups"]:
                description += " Returns reviewed storage fields only; omits authentication, open-ended configuration, mount options and container overrides. Secret references are names only."
            tools[name] = {"name": name, "description": description, "plugin": plugin,
                           "groupVersion": source["groupVersion"], "operationId": row["operationId"],
                           "schemaHash": digest, "inputSchema": contract["inputSchema"],
                           "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
                           "_meta": {"re8ch": {"plugin": plugin, "groupVersion": source["groupVersion"],
                                      "operationId": row["operationId"], "method": "GET", "path": row["path"],
                                      "schemaHash": digest, "httpConformance": "not_run", "semanticConformance": "not_run"}}}
    definitions = sorted([*tools.values(), ANATOMY_DIFF_TOOL, ANATOMY_DIFF_DELETE_TOOL], key=lambda tool: tool["name"])
    generated = {"version": 1, "cluster": matrix["cluster"], "tools": definitions}
    (ROOT / "operations/cloud-platform/plugin-operation-tools.json").write_text(json.dumps(generated, indent=2) + "\n")
    for relative in ["platform/apps/tenant-mcp/platform-modules.json", "clusters/re8ch/charts/birfucate-metering/files/tenant-platform-modules.json"]:
        path = ROOT / relative
        registry = json.loads(path.read_text())
        allowed = registry["backends"]["native"]["allowed"]
        # Remove every currently owned entry before inserting in stable order.
        # Otherwise new Mihomos entries move across Kubernetes entries on the
        # second run because dict.update preserves existing key positions.
        owned_names = previous_names | {tool["name"] for tool in MIHOMOS_TOOLS} | {ANATOMY_DIFF_TOOL["name"], ANATOMY_DIFF_DELETE_TOOL["name"]}
        for name in list(allowed):
            if name in owned_names:
                del allowed[name]
        allowed.update({name: True for name in sorted(tools)})
        allowed.update({tool["name"]: True for tool in MIHOMOS_TOOLS})
        allowed[ANATOMY_DIFF_TOOL["name"]] = True
        allowed[ANATOMY_DIFF_DELETE_TOOL["name"]] = True
        path.write_text(json.dumps(registry, indent=2) + "\n")
    registry = {"servers": [{"name": "com.re8ch/cluster-plugin-operations", "version": "0.1.0",
        "description": "Admin tools for RE8CH cluster plugins. Read operations available; full coverage in progress.",
        "remotes": [{"type": "streamable-http", "url": "https://operate.tools.re8ch.com/cloud/mcp"}],
        "_meta": {"io.modelcontextprotocol.registry/publisher-provided": {"io.github.stacklok": {
            "streamable-http": {"status": "active", "re8ch_capabilities": capability_inventory(matrix, definitions), "tool_definitions": [{key: tool[key] for key in ("name", "description", "inputSchema", "_meta")} for tool in definitions] + MIHOMOS_TOOLS}}}}}]}
    (ROOT / "operations/plugin-capabilities/registry.json").write_text(json.dumps(registry, indent=2) + "\n")
    # The file source consumes ToolHive's registry envelope, not the MCP API's
    # bare servers response. Keep the readable payload used by UI tooling.
    runtime_registry = {"version": "1.0.0", "meta": {"last_updated": "2026-10-04T00:00:00Z"}, "data": registry}
    (ROOT / "operations/plugin-capabilities/registry.runtime.json").write_text(json.dumps(runtime_registry, separators=(",", ":")) + "\n")
    return len(tools)


if __name__ == "__main__":
    print(f"Generated {generate()} reviewed read tools")
