#!/usr/bin/env python3
"""Check captured native operations with real Schemathesis; no API requests."""
import argparse
import json
from collections import Counter
from importlib.metadata import version
from pathlib import Path

import schemathesis
from schemathesis.core.result import Ok

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    matrix = json.loads((ROOT / "native-operation-matrix.json").read_text())
    results = []
    for source in matrix["sources"]:
        schema = schemathesis.openapi.from_path(ROOT / source["schemaFile"])
        operations = list(schema.get_all_operations())
        errors = [str(item.err()) for item in operations if not isinstance(item, Ok)]
        if errors:
            raise RuntimeError(f"{source['groupVersion']}: {errors}")
        actual = {(item.ok().method.lower(), item.ok().path, item.ok().definition.raw.get("operationId")) for item in operations}
        expected = {(row["method"], row["path"], row["operationId"]) for row in source["operations"]}
        if len(expected) != len(source["operations"]) or len(actual) != len(operations) or actual != expected:
            raise RuntimeError(f"{source['groupVersion']}: matrix does not match the native OpenAPI operation set")
        result = {"groupVersion": source["groupVersion"], "operations": len(operations),
                  "methods": dict(sorted(Counter(row["method"] for row in source["operations"]).items())),
                  "schemaEnumeration": "passed", "httpConformance": "not_run", "semanticConformance": "not_run"}
        results.append(result)
    report = {"schemathesisVersion": version("schemathesis"), "mode": "offline-schema-enumeration",
              "networkRequests": 0, "sources": results, "totalOperations": sum(row["operations"] for row in results)}
    controller_schema = schemathesis.openapi.from_path(ROOT.parent / "cloud-platform/mihomo-controller-openapi.json")
    controller_operations = list(controller_schema.get_all_operations())
    if any(not isinstance(item, Ok) for item in controller_operations):
        raise RuntimeError("Source-derived Mihomo contract could not be parsed")
    controller_actual = {(item.ok().method.lower(), item.ok().path, item.ok().definition.raw.get("operationId")) for item in controller_operations}
    controller_inventory = json.loads((ROOT / "mihomos-native-surfaces.json").read_text())
    controller_expected = {(row["method"].lower(), row["path"], row["operationId"]) for row in controller_inventory["operations"] if row.get("registration") == "implemented_not_deployed"}
    if controller_actual != controller_expected or len(controller_operations) != len(controller_expected):
        raise RuntimeError("Mihomo contract enumeration does not match implemented operation")
    report["sourceDerivedControllerSchemas"] = [{"plugin": "mihomos-cluster", "version": "v1.19.29", "operations": len(controller_operations),
        "schemaEnumeration": "passed", "httpConformance": "not_run", "semanticConformance": "not_run", "coverage": "partial"}]
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
