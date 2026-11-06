"""Summarize only release result artifacts; Action success is not runtime health."""
import argparse
import json
from pathlib import Path


def summarize(directory, conclusion, delivery=""):
    reports, invalid = [], False
    for path in sorted(Path(directory).rglob("result.json")):
        try:
            value = json.loads(path.read_text())
            if delivery and value.get("delivery") not in (None, delivery):
                invalid = True
                continue
            release = value.get("releaseId", value.get("release"))
            outcome = value.get("outcome")
            digest = value.get("observedDigest") or value.get("approvedDigest") or value.get("digest")
            health = value.get("health", {}).get("status")
            if not release or not outcome:
                invalid = True
                continue
            reports.append({"release": release, "digest": digest, "outcome": outcome,
                            "health": health, "warning": value.get("health", {}).get("reason", "") if value.get("health", {}).get("reason", "").startswith("Warning:") else "", "applied": bool(value.get("applied") or value.get("application", {}).get("appliedInventory"))})
        except (ValueError, TypeError, AttributeError):
            invalid = True
    state = "Unknown"
    if conclusion in ("failure", "timed_out", "action_required", "startup_failure"):
        state = "Failed"
    elif any(r["outcome"] in ("failed", "blocked", "rolled-back") for r in reports):
        state = "Failed"
    elif any(r["outcome"] in ("degraded", "unhealthy") or r["health"] in ("degraded", "unhealthy") or r["warning"] for r in reports):
        state = "Degraded"
    elif conclusion == "success" and reports and not invalid and all(
        (r["outcome"] == "healthy" and r["health"] == "healthy") or
        (r["outcome"] in ("aligned", "recovered") and r["health"] in (None, "healthy"))
        for r in reports
    ):
        state = "Healthy"
    return {"state": state, "reports": reports, "invalid": invalid,
            "action": "Reconcile" if any(r["applied"] for r in reports) else "Observe Only"}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("directory")
    p.add_argument("conclusion")
    p.add_argument("--delivery", default="")
    a = p.parse_args()
    print(json.dumps(summarize(a.directory, a.conclusion, a.delivery)))
