"""Immutable publication authority and strict, non-textual report projection."""
import json
import re
from contract import ContractError

COUNTS = ("generated", "withFailures", "withoutChecks", "uniqueFailures", "operationsTotal",
          "operationsSelected", "operationsTested", "operationsErrored", "operationsSkipped")
STATES = {"not_run", "passed", "failed", "skipped"}


def authority(target):
    spec = target["spec"]
    value = {"repository": spec["repository"], "commit": spec["commit"], "branch": spec["branch"],
             "imageDigest": spec.get("imageDigest", ""), "schemaSha256": spec["schema"]["sha256"],
             "installationId": str(spec.get("github", {}).get("installationId", ""))}
    patterns = {"repository": r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", "commit": r"[a-f0-9]{40}",
                "branch": r"[A-Za-z0-9_./-]+", "imageDigest": r"sha256:[a-f0-9]{64}",
                "schemaSha256": r"[a-f0-9]{64}", "installationId": r"[0-9]+"}
    if any(not re.fullmatch(pattern, value[key]) for key, pattern in patterns.items()):
        raise ContractError("invalid publication authority")
    return value


def publication_target(run):
    a = run["spec"].get("publication")
    if not isinstance(a, dict):
        raise ContractError("run has no immutable publication authority")
    target = {"metadata": {"name": run["spec"]["targetRef"]}, "spec": {
        **a, "schema": {"sha256": a.get("schemaSha256")},
        "github": {"installationId": a.get("installationId")}}}
    if authority(target) != a or a["commit"] != run["spec"].get("commit") or a["imageDigest"] != run["spec"].get("imageDigest"):
        raise ContractError("run publication authority mismatch")
    return target


def project_report(raw, run):
    """Never trust arbitrary runner log JSON, even if it calls itself sanitized."""
    a = publication_target(run)["spec"]
    expected = {"target": run["spec"]["targetRef"], "runId": run["metadata"]["name"],
                "mode": run["spec"]["mode"], "schemaSha256": a["schemaSha256"]}
    if any(raw.get(k) != v for k, v in expected.items()):
        raise ContractError("report identity mismatch")
    if raw.get("version") != 1 or raw.get("engine") != "schemathesis" or raw.get("engineVersion") != "4.29.1" or raw.get("status") not in {"passed", "failed"}:
        raise ContractError("invalid report engine or status")
    counts = {key: raw.get("counts", {}).get(key) for key in COUNTS}
    if any(type(v) is not int or not 0 <= v <= 1000000 for v in counts.values()):
        raise ContractError("invalid report counts")
    lifecycle = {key: raw.get("lifecycle", {}).get(key) for key in ("setup", "test", "cleanup", "reversal")}
    if any(v not in STATES for v in lifecycle.values()):
        raise ContractError("invalid lifecycle state")
    seed, code = raw.get("seed"), raw.get("exitCode")
    if type(seed) is not int or not 0 <= seed < 2**64 or type(code) is not int or not -255 <= code <= 255:
        raise ContractError("invalid report numeric fields")
    fingerprints = raw.get("failureFingerprints", [])
    if not isinstance(fingerprints, list) or len(fingerprints) > 50 or any(not isinstance(x, str) or not re.fullmatch(r"[a-f0-9]{20}", x) for x in fingerprints):
        raise ContractError("invalid fingerprints")
    refs = raw.get("auditRefs", [])
    if not isinstance(refs, list) or len(refs) > 20 or any(not isinstance(x, str) or not re.fullmatch(r"audit://[A-Za-z0-9_./-]{1,200}", x) for x in refs):
        raise ContractError("invalid audit references")
    if raw["status"] == "passed" and (code != 0 or counts["operationsTested"] != counts["operationsSelected"] or counts["operationsSelected"] == 0 or counts["generated"] <= counts["withoutChecks"] or any(counts[k] for k in ("withFailures", "uniqueFailures", "operationsErrored", "operationsSkipped")) or fingerprints or "failed" in lifecycle.values()):
        raise ContractError("inconsistent passing report")
    return {**expected, "version": 1, "engine": "schemathesis", "engineVersion": "4.29.1",
            "status": raw["status"], "seed": seed, "exitCode": code, "counts": counts,
            "failureFingerprints": fingerprints, "lifecycle": lifecycle, "auditRefs": refs}


def failed_report(run):
    return {"version": 1, "target": run["spec"]["targetRef"], "runId": run["metadata"]["name"],
            "mode": run["spec"]["mode"], "engine": "schemathesis", "engineVersion": "4.29.1",
            "schemaSha256": run["spec"].get("publication", {}).get("schemaSha256", "0" * 64),
            "status": "failed", "seed": 0, "exitCode": 1, "counts": dict.fromkeys(COUNTS, 0),
            "failureFingerprints": [], "lifecycle": dict.fromkeys(("setup", "test", "cleanup", "reversal"), "failed"), "auditRefs": []}


def delivery_record(run, report, binding, expires):
    a = publication_target(run)["spec"]
    if not binding or any(not str(binding.get(k, "")).isdecimal() for k in ("repositoryId", "ownerId")):
        raise ContractError("unreviewed delivery repository")
    clean = project_report(report, run)
    return {"repositoryId": str(binding["repositoryId"]), "ownerId": str(binding["ownerId"]),
            "expiresAt": expires, "claimed": False,
            "report": {"runId": clean["runId"], "target": clean["target"], "repository": a["repository"],
                       "commit": a["commit"], "imageDigest": a["imageDigest"], "schemaSha256": clean["schemaSha256"],
                       "passed": clean["status"] == "passed", "operations": clean["counts"]["operationsTested"],
                       "summary": clean}}
