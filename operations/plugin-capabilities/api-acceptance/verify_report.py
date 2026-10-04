"""Validate live MCP reports against explicit API cases; missing results fail closed."""
import argparse
import json
from pathlib import Path

CHECKS = {"status_code_conformance", "content_type_conformance",
          "response_headers_conformance", "response_schema_conformance"}


def verify(cases, results):
    expected = {case["id"]: case for case in cases}
    if len(expected) != len(cases):
        raise ValueError("duplicate case IDs")
    seen = set()
    failures = []
    for row in results:
        key = row.get("caseId")
        if key not in expected or key in seen:
            failures.append({"caseId": key, "reason": "unknown or duplicate result"})
            continue
        seen.add(key)
        case = expected[key]
        envelope = row.get("result", {})
        try:
            if envelope.get("isError"):
                raise ValueError("tool error")
            report = envelope.get("structuredContent")
            if report is None:
                report = json.loads(next(x["text"] for x in envelope["content"] if x["type"] == "text"))
            checks = report["checks"]
            valid = (
                report["operationId"] == case["arguments"]["operationId"]
                and report["schemaHash"] == case["arguments"]["schemaHash"]
                and report["cluster"] == "re8ch-k3s"
                and report["statusCode"] == case["expectedStatus"]
                and report["engine"] == "schemathesis"
                and report["scope"] == "one_finite_GET_response"
                and report["httpConformance"] == "passed"
                and report["bodyReturned"] is False
                and report["networkRequests"] == 1
                and isinstance(report["auditRef"], str)
                and report["auditRef"].startswith("audit://operations/op_")
                and len(checks) == len(CHECKS)
                and {x["check"] for x in checks} == CHECKS
                and all(x["status"] == "passed" for x in checks))
            if not valid:
                raise ValueError("contract assertion failed")
        except (AttributeError, KeyError, TypeError, ValueError, StopIteration):
            failures.append({"caseId": key, "reason": "invalid or failed API report"})
    for key in expected.keys() - seen:
        failures.append({"caseId": key, "reason": "not run"})
    return {"total": len(cases), "passed": len(seen - {f["caseId"] for f in failures}),
            "failures": failures, "complete": not failures}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("cases.json"))
    args = parser.parse_args()
    result = verify(json.loads(args.cases.read_text())["cases"], json.loads(args.report.read_text()))
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["complete"] else 1)

