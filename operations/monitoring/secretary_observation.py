"""Publish synthetic failure evidence to Secretary's signed observation ledger."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
import urllib.request
from pathlib import Path


def publish(*, kind: str, subject: str, run_id: str, summary: str,
            artifact_ref: str, metadata: dict[str, str] | None = None) -> bool:
    endpoint = os.environ.get("SECRETARY_OBSERVATION_URL", "")
    secret = os.environ.get("SECRETARY_WEBHOOK_SECRET", "")
    if not endpoint or not secret:
        print("Secretary observation not configured; failure remains in Actions evidence")
        return False
    fingerprint = hashlib.sha256(f"{kind}:{subject}".encode()).hexdigest()[:24]
    payload = {
        "interaction_id": f"synthetic:{fingerprint}",
        "native_type": kind, "native_status": "failed", "relation": "health_observed",
        "source": {"kind": "observer", "id": "github-hosted-synthetic-monitor", "runtime_id": "github-actions"},
        "target": {"kind": "service", "id": subject, "runtime_id": "public-edge"},
        "runtime": {"kind": "runtime", "id": "github-actions"},
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "authority": "tool_observation",
        "evidence": {"strength": "supporting", "failure_domain": "github-hosted-runner",
                     "method": kind, "confidence": 0.8, "artifact_ref": artifact_ref},
        "idempotency_key": f"synthetic:{fingerprint}:{run_id}",
        "metadata": {"severity": "critical", "summary": summary, **(metadata or {})},
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    request = urllib.request.Request(endpoint, data=raw, method="POST", headers={
        "Content-Type": "application/json", "X-Secretary-Timestamp": timestamp,
        "X-Secretary-Signature": signature,
    })
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status != 202:
            raise RuntimeError(f"Secretary returned HTTP {response.status}")
    return True


def publish_k6_failures(summary: dict, catalog: dict, run_id: str) -> int:
    checks = summary.get("root_group", {}).get("checks", {})
    failures = 0
    for probe in catalog["probes"]:
        if probe["vantage"] != "external":
            continue
        check = checks.get(f"{probe['id']} contract")
        if check is None or check.get("fails", 0) == 0:
            continue
        failures += 1
        publish(kind="k6.contract", subject=f"public/{probe['id']}", run_id=run_id,
                summary=f"{probe['id']}: {check['fails']} contract checks failed",
                artifact_ref=f"github-actions:{run_id}:k6-contract-summary",
                metadata={"owner": probe["owner"], "url": probe["url"]})
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("summary", type=Path)
    parser.add_argument("--catalog", type=Path, default=Path("clusters/re8ch/components/synthetic-monitoring/contracts.json"))
    args = parser.parse_args()
    publish_k6_failures(json.loads(args.summary.read_text()),
                        json.loads(args.catalog.read_text()), os.environ.get("GITHUB_RUN_ID", "local"))


if __name__ == "__main__":
    main()
