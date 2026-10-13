#!/usr/bin/env python3
"""Validate reusable k6 contracts, coordinated schedules, and append-only history."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = "clusters/re8ch/components/synthetic-monitoring/contracts.json"
DEFAULT_REVISIONS = "operations/k6/revisions.json"
DEFAULT_K8S = "clusters/re8ch/components/synthetic-monitoring/cluster-cronjob.yaml"
DEFAULT_WORKFLOW = ".github/workflows/k6-contracts.yml"


def repo_path(relative):
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError(f"path escapes repository: {relative}")
    return path


def read_json(relative):
    return json.loads(repo_path(relative).read_text(encoding="utf-8"))


def validate_catalog(catalog):
    assert isinstance(catalog["probes"], list) and catalog["probes"], "probes required"
    cadences = catalog["cadences"]
    assert set(cadences) == {"fast", "standard"}, "fast and standard cadences required"
    assert cadences["fast"]["periodSeconds"] == 300, "fast cadence must be five minutes"
    assert cadences["standard"]["periodSeconds"] == 1800, "standard cadence must be thirty minutes"
    for cadence in cadences.values():
        assert cadence["externalCron"] != cadence["internalCron"], "vantage phases must differ"
    ids = set()
    for probe in catalog["probes"]:
        assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", probe["id"]), "invalid probe ID"
        assert probe["id"] not in ids, "duplicate probe ID"
        ids.add(probe["id"])
        assert re.fullmatch(r"[a-z0-9-]+/[a-z0-9-]+", probe["owner"]), "owner must be org/module"
        assert probe["cadence"] in cadences, "unknown cadence"
        assert probe["vantage"] in ("external", "internal"), "unknown vantage"
        url = urlparse(probe["url"])
        assert url.hostname and not url.username and not url.password and not url.query and not url.fragment, "invalid probe URL"
        if probe["vantage"] == "external":
            assert url.scheme == "https" and not url.hostname.endswith(".cluster.local"), "external probe must use public HTTPS"
            assert any(key in probe for key in ("jsonField", "header", "contentTypeContains", "bodyContains", "bodyNotContains")), "external probe needs semantic assertion"
        else:
            assert url.scheme == "http" and url.hostname.endswith(".svc.cluster.local"), "internal probe must use service DNS"
        assert type(probe["status"]) is int and 100 <= probe["status"] <= 599, "invalid status contract"
        assert ("jsonField" in probe) == ("equals" in probe), "incomplete JSON assertion"
        assert ("header" in probe) == ("contains" in probe), "incomplete header assertion"
        for key in ("contentTypeContains", "bodyContains", "bodyNotContains"):
            if key in probe:
                assert isinstance(probe[key], str) and probe[key], f"{key} must be a non-empty string"


def validate_schedules(catalog, k8s_path, workflow_path):
    jobs = [doc for doc in yaml.safe_load_all(repo_path(k8s_path).read_text()) if doc and doc.get("kind") == "CronJob"]
    assert {job["metadata"]["name"] for job in jobs} == {"k6-contracts", "k6-contracts-fast"}, "preserve original CronJob identity when prune is disabled"
    actual = {
        ("standard" if job["metadata"]["name"] == "k6-contracts" else job["metadata"]["name"].removeprefix("k6-contracts-")): job
        for job in jobs
    }
    assert set(actual) == set(catalog["cadences"]), "one internal CronJob required per cadence"
    for cadence, job in actual.items():
        assert job["spec"]["schedule"] == catalog["cadences"][cadence]["internalCron"], "internal schedule drift"
        assert job["spec"]["concurrencyPolicy"] == "Forbid", "internal overlap is forbidden"
        container = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
        env = {item["name"]: item["value"] for item in container["env"]}
        assert env == {"PROBE_VANTAGE": "internal", "PROBE_CADENCE": cadence}, "internal selector drift"
    workflow = yaml.safe_load(repo_path(workflow_path).read_text())
    trigger = workflow.get("on", workflow.get(True))  # PyYAML 1.1 treats 'on' as a boolean.
    actual_crons = {item["cron"] for item in trigger["schedule"]}
    expected_crons = {item["externalCron"] for item in catalog["cadences"].values()}
    assert actual_crons == expected_crons, "external schedule drift"


def validate_revisions(raw, revisions):
    assert revisions and len(revisions) == json.loads(raw)["version"], "version must match revision count"
    previous = None
    for index, revision in enumerate(revisions, 1):
        assert revision["number"] == index, "revision sequence must be continuous"
        assert revision["previousSha256"] == previous, "revision hash chain broken"
        assert len(revision["reason"].strip()) >= 12, "revision reason required"
        digest = revision["contractSha256"]
        assert re.fullmatch(r"[0-9a-f]{64}", digest), "invalid digest"
        previous = digest
    assert previous == hashlib.sha256(raw).hexdigest(), "catalog digest does not match latest revision"


def git_show(base, relative):
    return subprocess.run(
        ["git", "show", f"{base}:{relative}"], cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default=DEFAULT_CATALOG, help="catalog path relative to repository root")
    parser.add_argument("--revisions", default=DEFAULT_REVISIONS, help="revision path relative to repository root")
    parser.add_argument("--k8s", default=DEFAULT_K8S, help="internal CronJob manifest")
    parser.add_argument("--workflow", default=DEFAULT_WORKFLOW, help="external GitHub workflow")
    parser.add_argument("--external-only", action="store_true", help="skip Kubernetes schedule comparison")
    parser.add_argument("--base", help="PR base commit for append-only validation")
    args = parser.parse_args()
    raw = repo_path(args.catalog).read_bytes()
    catalog = json.loads(raw)
    revisions = read_json(args.revisions)["revisions"]
    validate_catalog(catalog)
    validate_revisions(raw, revisions)
    if not args.external_only:
        validate_schedules(catalog, args.k8s, args.workflow)
    if args.base:
        old_revisions = git_show(args.base, args.revisions)
        if old_revisions.returncode == 0:
            past = json.loads(old_revisions.stdout)["revisions"]
            assert revisions[:len(past)] == past, "past revisions may not be edited"
        else:
            past = []
        old_contract = git_show(args.base, args.catalog)
        if old_contract.returncode == 0 and old_contract.stdout != raw:
            assert len(revisions) > len(past), "contract change requires a new revision"
    print(f"Validated {len(revisions)} contract revision(s) and {len(catalog['probes'])} probe(s)")


if __name__ == "__main__":
    main()
