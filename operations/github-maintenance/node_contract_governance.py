#!/usr/bin/env python3
"""Turn sanitized NodeContract evidence into quiet, deduplicated governance."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


REPO = os.environ.get("GITHUB_REPOSITORY", "re8ch/service-backend")
OWNER = REPO.split("/", 1)[0]
PROJECT_NUMBER = os.environ.get("GITOPS_MAINTENANCE_PROJECT", "1")
TITLE_PREFIX = "[NodeContract drift]"
ACTIVE_TITLE = f"{TITLE_PREFIX} active reconciliation"
EVIDENCE_SIZE = 256 * 1024


def evidence_cipher():
    from cryptography.fernet import Fernet
    # Dedicated random Environment secret; never derive encryption from public digests.
    key = os.environ["NODE_EVIDENCE_KEY"]
    if len(base64.urlsafe_b64decode(key)) != 32:
        raise ValueError("Invalid evidence key")
    return Fernet(key.encode())


def seal_evidence(source: Path, destination: Path, run_id: str) -> None:
    evidence = json.loads(source.read_text(encoding="utf-8"))
    envelope = json.dumps({"repository": REPO, "runId": run_id, "evidence": evidence}).encode()
    if len(envelope) >= EVIDENCE_SIZE:
        raise ValueError("Evidence exceeds sealed size limit")
    # Constant-size padding avoids exposing node/drift counts through artifact length.
    token = evidence_cipher().encrypt(envelope.ljust(EVIDENCE_SIZE, b"\0"))
    with destination.open("xb") as stream:
        stream.write(token)


def load_evidence(path: Path, run_id: str) -> dict[str, Any]:
    if path.stat().st_size > EVIDENCE_SIZE * 2:
        raise ValueError("Invalid sealed evidence")
    raw = evidence_cipher().decrypt(path.read_bytes())
    if len(raw) != EVIDENCE_SIZE:
        raise ValueError("Invalid sealed evidence size")
    envelope = json.loads(raw.rstrip(b"\0"))
    if envelope["repository"] != REPO or envelope["runId"] != run_id:
        raise ValueError("Evidence provenance mismatch")
    evidence = envelope["evidence"]
    if evidence.get("schemaVersion") != "v1alpha1" or not isinstance(evidence.get("drift"), list):
        raise ValueError("Invalid evidence schema")
    return evidence


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def api(endpoint: str) -> Any:
    return json.loads(gh("api", endpoint))


def historical_evidence() -> list[tuple[dt.datetime, dict[str, Any]]]:
    """Return the uninterrupted history of prior, valid observer evidence.

    A failed run without an evidence artifact breaks the streak. This prevents
    transport, authentication, or workflow failures from being misclassified as
    repeated declaration drift.
    """
    current = str(os.environ.get("GITHUB_RUN_ID", ""))
    result = api(f"repos/{REPO}/actions/workflows/node-contract-observer.yml/runs?branch=main&per_page=50")
    completed = [
        run for run in result.get("workflow_runs", [])
        if str(run.get("id")) != current and run.get("status") == "completed"
    ]
    history: list[tuple[dt.datetime, dict[str, Any]]] = []
    for run in completed:
        run_id = str(run["id"])
        artifact_name = f"node-contract-evidence-sealed-{run_id}"
        artifacts = api(f"repos/{REPO}/actions/runs/{run_id}/artifacts").get("artifacts", [])
        if not any(item.get("name") == artifact_name and not item.get("expired") for item in artifacts):
            break
        with tempfile.TemporaryDirectory(prefix="node-contract-history-") as directory:
            try:
                gh("run", "download", run_id, "--repo", REPO, "--name", artifact_name, "--dir", directory)
                evidence = load_evidence(Path(directory) / "node-contract-evidence.fernet", run_id)
            except Exception:
                # Missing, legacy, rotated or tampered history breaks the streak; never fall back to plaintext.
                break
        created_at = dt.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        history.append((created_at, evidence))
    return history


def persistent_keys(
    evidence: dict[str, Any],
    history: list[tuple[dt.datetime, dict[str, Any]]],
    *,
    now: dt.datetime | None = None,
) -> set[str]:
    """Select drift keys seen in 3 valid observations or continuously for 24h."""
    current = {
        issue_key(item) for item in evidence["drift"]
        if item["severity"] == "persistent"
    }
    if not current:
        return set()
    now = now or dt.datetime.now(dt.timezone.utc)
    occurrences = {key: 1 for key in current}
    oldest = {key: now for key in current}
    candidates = set(current)
    for created_at, previous in history:
        previous_keys = {
            issue_key(item) for item in previous.get("drift", [])
            if item.get("severity") == "persistent"
        }
        candidates &= previous_keys
        if not candidates:
            break
        for key in candidates:
            occurrences[key] += 1
            oldest[key] = created_at
    return {
        key for key in current
        if occurrences[key] >= 3 or now - oldest[key] >= dt.timedelta(hours=24)
    }


def issue_key(drift: dict[str, Any]) -> str:
    parts = [drift["type"], drift.get("contractRef", "cluster"), drift.get("fieldRef", "")]
    return ":".join(part for part in parts if part)


def render_body(actionable: list[dict[str, Any]], evidence: dict[str, Any]) -> str:
    return f"""## NodeContract reconciliation review

The protected observer requires internal review.
Observer run: `{os.environ.get('GITHUB_RUN_ID', 'unknown')}`

Detailed evidence is encrypted before upload. Public Actions artifacts are not
an internal access boundary. Authorized operators inspect runtime details through
the authenticated platform interface; no node identifiers, counts, conditions or
drift categories are published in this issue.

Project status is governance metadata only and never grants Kubernetes access.
"""


def existing_issues() -> list[dict[str, Any]]:
    return [
        item for item in api(f"repos/{REPO}/issues?state=all&labels=actor%2Fautomation&per_page=100")
        if "pull_request" not in item and item.get("title", "").startswith(TITLE_PREFIX)
    ]


def publish(evidence: dict[str, Any]) -> None:
    eligible = persistent_keys(evidence, historical_evidence())
    actionable = [
        item for item in evidence["drift"]
        if item["severity"] == "immediate" or issue_key(item) in eligible
    ]
    known = existing_issues()
    by_title = {item["title"]: item for item in known}
    active_titles: set[str] = set()
    if actionable:
        active_titles.add(ACTIVE_TITLE)
        body = render_body(actionable, evidence)
        old = by_title.get(ACTIVE_TITLE)
        if old:
            gh("api", "--method", "PATCH", f"repos/{REPO}/issues/{old['number']}", "-f", f"body={body}", "-f", "state=open")
        else:
            created = json.loads(gh(
                "api", "--method", "POST", f"repos/{REPO}/issues",
                "-f", f"title={ACTIVE_TITLE}", "-f", f"body={body}",
                "-f", "labels[]=type/maintenance", "-f", "labels[]=actor/automation",
            ))
            gh("project", "item-add", PROJECT_NUMBER, "--owner", OWNER, "--url", created["html_url"])
    for old in known:
        if old.get("state") == "open" and old["title"] not in active_titles:
            gh("api", "--method", "PATCH", f"repos/{REPO}/issues/{old['number']}", "-f", "state=closed", "-f", "state_reason=completed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--seal", type=Path, help="Encrypt evidence to this output instead of publishing")
    args = parser.parse_args()
    try:
        run_id = os.environ["GITHUB_RUN_ID"]
        if args.seal:
            seal_evidence(args.evidence, args.seal, run_id)
        else:
            publish(load_evidence(args.evidence, run_id))
    except Exception:
        raise SystemExit("Protected evidence operation failed; use authenticated diagnostics") from None


if __name__ == "__main__":
    main()
