#!/usr/bin/env python3
"""Reconcile declared plugins with implemented registrations, never infer live coverage."""
import argparse
import json
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
ALIASES = {"advanced-fabrics": "advfab", "rook-ceph": "rook-ceph", "mihomos-cluster": "mihomos-cluster"}


def audit(root=ROOT):
    definitions = {}
    for path in sorted((root / "plugins").glob("*.yaml")):
        document = yaml.safe_load(path.read_text())
        entries = document.get("items", []) if document.get("kind") == "PluginDefinitionList" else [document]
        for entry in entries:
            if entry["metadata"]["name"] in definitions:
                raise ValueError("Duplicate PluginDefinition: " + entry["metadata"]["name"])
            definitions[entry["metadata"]["name"]] = (entry, str(path.relative_to(root)))
    registry = json.loads((root / "operations/plugin-capabilities/registry.json").read_text())
    published = registry["servers"][0]["_meta"]["io.modelcontextprotocol.registry/publisher-provided"]["io.github.stacklok"]["streamable-http"]["tool_definitions"]
    by_plugin = {}
    for tool in published:
        plugin = tool.get("_meta", {}).get("re8ch", {}).get("plugin")
        if tool["name"].startswith("mihomos."):
            plugin = "mihomos-cluster"
        if plugin is None:
            raise ValueError("Unclassified directory tool: " + tool["name"])
        if plugin == "advfab-legacy":
            plugin = "advfab"
        by_plugin.setdefault(plugin, []).append(tool["name"])
    rows = []
    for name, (definition, source_path) in definitions.items():
        plugin = ALIASES.get(name, name)
        tools = sorted(by_plugin.get(plugin, []))
        rows.append({"plugin": name, "definition": source_path,
                     "publicationEnabled": definition["spec"]["publication"]["enabled"],
                     "implementedDirectoryTools": tools,
                     "declaredOperations": definition["spec"]["policy"]["allowedOperations"],
                     "gaps": (["no_operation_adapter_in_this_registry"] if not tools else ["operation_coverage_incomplete"])
                             + (["unverified_cli_image_digest"] if "REQUIRES_VERIFIED_DIGEST" in json.dumps(definition) else []),
                     "liveRegistration": "not_verified", "httpConformance": "not_run", "semanticConformance": "not_run"})
    declared_plugins = {ALIASES.get(name, name) for name in definitions}
    for plugin in sorted(set(by_plugin) - declared_plugins):
        rows.append({"plugin": plugin, "definition": None, "implementedDirectoryTools": sorted(by_plugin[plugin]),
                     "gaps": ["missing_root_plugin_definition", "operation_coverage_incomplete"],
                     "liveRegistration": "not_verified", "httpConformance": "not_run", "semanticConformance": "not_run"})
    governance = yaml.safe_load((root / "inventory/platform/governance/clusters.yaml").read_text())
    external = yaml.safe_load((root / "inventory/platform/controlled-subclusters.yaml").read_text())
    clusters = [{"cluster": item["id"], "authority": "platform-governance", "nativeInventory": "partial", "coverage": "not_complete"} for item in governance["clusters"]]
    clusters += [{"cluster": item["name"], "authority": "external-owner", "declaredStatus": item["status"],
                  "nativeInventory": "not_verified", "coverage": "owner_access_not_verified"} for item in external["spec"]["clusters"]]
    discovery = json.loads((root / "operations/plugin-capabilities/live-discovery-report.json").read_text())
    matrix = json.loads((root / "operations/plugin-capabilities/native-operation-matrix.json").read_text())
    captured = {source["groupVersion"] for source in matrix["sources"]}
    schema_discovery = {"cluster": discovery["cluster"], "capturedAt": discovery["capturedAt"],
        "auditRef": discovery["auditRef"], "indexEntries": discovery["indexEntries"],
        "groupVersionEntries": discovery["groupVersionEntries"],
        "capturedSchemas": sum(row["coverage"] == "captured_schema" for row in discovery["schemas"]),
        "uncapturedSchemas": sum(row["coverage"] == "not_captured" for row in discovery["schemas"]),
        "currentCapturedSchemas": sum(row["groupVersion"] in captured for row in discovery["schemas"]),
        "currentUncapturedSchemas": sum(row["groupVersion"] not in captured for row in discovery["schemas"]),
        "pluginIdentityCoverage": "not_proven"}
    return {"nativeSchemaDiscovery": schema_discovery, "mode": "offline-declaration-reconciliation", "networkRequests": 0,
            "scope": "declared_plugins_and_governed_clusters_not_all_live_installations", "plugins": rows, "clusters": clusters,
            "completion": "not_proven"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = audit()
    output = json.dumps(result, indent=2) + "\n"
    if args.report:
        args.report.write_text(output)
    print(json.dumps({"plugins": len(result["plugins"]), "clusters": len(result["clusters"]), "completion": result["completion"]}))
