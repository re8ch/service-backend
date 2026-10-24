#!/usr/bin/env python3
"""Enforce the bounded, self-hosted CodeQL contract for this control plane."""

from pathlib import Path

import yaml


WORKFLOW = Path(".github/workflows/codeql-control-plane.yml")
CONFIG = Path(".github/codeql/control-plane.yml")
RUNNER_LABELS = {"self-hosted", "Linux", "X64", "restricted-node-6dc3f140f9", "untrusted-build"}
LANGUAGES = ["actions", "python"]
SCAN_PATHS = [".github/workflows", "operations", "tests"]
EVENT_PATHS = [
    ".github/workflows/**",
    ".github/codeql/**",
    "operations/**/*.py",
    "tests/**/*.py",
]
CODEQL_SHA = "9b3254085494b55a0f7c4c2b3d2d52cc712ce370"


def load(path: Path) -> dict:
    document = yaml.safe_load(path.read_text())
    assert isinstance(document, dict), f"{path} must contain a YAML mapping"
    return document


def main() -> None:
    workflow = load(WORKFLOW)
    config = load(CONFIG)
    raw = (WORKFLOW.read_text() + "\n" + CONFIG.read_text()).lower()

    for forbidden in ("swift", "macos-", "ubuntu-", "windows-", "autobuild"):
        assert forbidden not in raw, f"CodeQL contract must not contain {forbidden!r}"

    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict), "CodeQL triggers must be an explicit mapping"
    assert set(triggers) == {"pull_request", "push", "schedule", "workflow_dispatch"}
    assert triggers["pull_request"]["paths"] == EVENT_PATHS
    assert triggers["push"]["branches"] == ["main"]
    assert triggers["push"]["paths"] == EVENT_PATHS
    assert len(triggers["schedule"]) == 1, "CodeQL must have one bounded weekly schedule"

    assert workflow["permissions"] == {"contents": "read", "security-events": "write"}
    assert set(workflow["jobs"]) == {"analyze"}
    job = workflow["jobs"]["analyze"]
    assert job["if"] == "vars.CODEQL_CONTROL_PLANE_ENABLED == 'true'"
    assert set(job["runs-on"]) == RUNNER_LABELS
    assert job["timeout-minutes"] <= 30
    assert job["strategy"]["matrix"]["language"] == LANGUAGES

    codeql_steps = [
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("github/codeql-action/")
    ]
    assert [step["uses"] for step in codeql_steps] == [
        f"github/codeql-action/init@{CODEQL_SHA}",
        f"github/codeql-action/analyze@{CODEQL_SHA}",
    ]
    assert codeql_steps[0]["with"] == {
        "languages": "${{ matrix.language }}",
        "config-file": "./.github/codeql/control-plane.yml",
    }

    assert config["paths"] == SCAN_PATHS
    assert "queries" not in config, "default security queries are the compute budget"


if __name__ == "__main__":
    main()

