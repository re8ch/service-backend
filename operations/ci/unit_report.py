"""Opt-in pytest observer. It never selects, skips or reorders tests."""
from __future__ import annotations

from collections import Counter
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time

import pytest
import yaml

from . import unit_catalog

VERSIONS = {
    "git": ["--version"], "helm": ["version", "--short"],
    "kubectl": ["version", "--client", "-o", "json"],
    "kustomize": ["version"], "node": ["--version"],
}


def outcome(phases):
    if any(p["outcome"] == "failed" for p in phases):
        return "failed"
    if any(p["xfail"] for p in phases):
        return "xfail-or-xpass"
    if any(p["outcome"] == "skipped" for p in phases):
        return "skipped"
    if {p["phase"] for p in phases if p["outcome"] == "passed"} == {"setup", "call", "teardown"}:
        return "passed"
    return "incomplete"


def toolchain():
    tools = {}
    for tool, args in VERSIONS.items():
        executable = shutil.which(tool)
        if executable is None:
            tools[tool] = {"available": False}
            continue
        try:
            result = subprocess.run([executable, *args], capture_output=True, text=True, timeout=5)
            tools[tool] = {"available": True, "exit_code": result.returncode,
                           "version": result.stdout.strip() if result.returncode == 0 else None}
        except (OSError, subprocess.TimeoutExpired):
            tools[tool] = {"available": True, "version": None}
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "os": platform.system(), "arch": platform.machine(), "yaml": yaml.__version__,
            "libyaml": bool(getattr(yaml, "__with_libyaml__", False)), "tools": tools,
            "packages": dict(sorted((dist.metadata["Name"], dist.version)
                                    for dist in importlib.metadata.distributions() if dist.metadata["Name"]))}


def pytest_addoption(parser):
    parser.addoption("--unit-report-dir", help="Write unit observations without changing execution")


def pytest_configure(config):
    if directory := config.getoption("--unit-report-dir"):
        config.pluginmanager.register(Observer(Path(directory), config), "unit-observer")


class Observer:
    def __init__(self, directory, config):
        self.directory = directory
        self.root = Path(config.rootpath)
        self.config = config
        self.started = time.perf_counter()
        self.cases = {}
        self.collection_errors = []
        self.collection_seconds = None
        self.selected = []

    def pytest_collection_finish(self, session):
        self.collection_seconds = time.perf_counter() - self.started
        self.selected = [item.nodeid for item in session.items]
        for nodeid in self.selected:
            self.cases[nodeid] = []

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_errors.append(report.nodeid)

    def pytest_runtest_logreport(self, report):
        self.cases.setdefault(report.nodeid, []).append({
            "phase": report.when, "outcome": report.outcome, "seconds": report.duration,
            "xfail": hasattr(report, "wasxfail"),
        })

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session, exitstatus):
        elapsed = time.perf_counter() - self.started
        if raw := os.environ.get("UNIT_PLAN"):
            plan = json.loads(raw)
        else:
            plan = {"paths": [], "full": True, "risky_unknown": [], "jobs": {"unit": True},
                    "tests": sorted({nodeid.split("::", 1)[0] for nodeid in self.selected}),
                    "domains": [], "focused_suites": []}
        observation = unit_catalog.describe(plan, self.root)
        for nodeid, phases in self.cases.items():
            test = nodeid.split("::", 1)[0]
            if test not in observation["units"]:
                observation["units"][test] = {"status": "planned", "reasons": [{"code": "collected"}],
                                              "declaration": "legacy-domain-selection", "inputs": [], "reuse": "never"}
            unit = observation["units"][test]
            unit.setdefault("cases", {})[nodeid] = phases
        for test, unit in observation["units"].items():
            cases = unit.get("cases", {})
            phases = [phase for records in cases.values() for phase in records]
            unit["outcomes"] = dict(Counter(outcome(records) for records in cases.values()))
            unit["seconds"] = {name: sum(p["seconds"] for p in phases if p["phase"] == name)
                               for name in ("setup", "call", "teardown")}
            if any(p["outcome"] == "failed" for p in phases) or any(
                node == test or node.startswith(test + "::") for node in self.collection_errors
            ):
                unit["status"] = "failed"
            elif cases:
                unit["status"] = "executed" if all(
                    records and records[-1]["phase"] == "teardown" for records in cases.values()
                ) else "blocked"
            elif unit["status"] == "planned":
                unit["status"] = "blocked"
        observation.update({"exit_code": int(exitstatus), "pytest_seconds": elapsed,
                            "collection_seconds": self.collection_seconds,
                            "input_snapshot": getattr(self.config, "_validation_input_statistics", None),
                            "collection_errors": self.collection_errors, "toolchain": toolchain(),
                            "identity": {key: os.environ.get(key) for key in (
                                "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_REPOSITORY",
                                "GITHUB_WORKFLOW", "GITHUB_EVENT_NAME", "RUNNER_NAME", "RUNNER_OS", "RUNNER_ARCH")}})
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "units.json").write_text(json.dumps(observation, indent=2, sort_keys=True) + "\n")
        if path := os.environ.get("GITHUB_STEP_SUMMARY"):
            counts = Counter(unit["status"] for unit in observation["units"].values())
            with open(path, "a") as stream:
                stream.write(f"### Unit execution\n\nExit code: {int(exitstatus)}; pytest: {elapsed:.2f}s; "
                             f"status counts: {dict(counts)}. Full phase timings and toolchain: unit-observation artifact.\n\n")
                if statistics := observation["input_snapshot"]:
                    stream.write(f"Session input parsing: {statistics['parsed']} parsed, {statistics['reused']} reused. "
                                 "Reuse requires identical content and parser; every selected test still executes.\n\n")
                stream.write(unit_catalog.summary(observation))
