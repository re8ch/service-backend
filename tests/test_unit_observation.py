"""Observation must preserve execution and distinguish failures from missing work."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from operations.ci import impact, unit_catalog, unit_report

ROOT = Path(__file__).resolve().parents[1]


def test_declarations_cover_first_batch_without_enabling_reuse():
    catalog = unit_catalog.load_catalog()
    assert len(catalog["validation_units"]) == 15
    assert set(catalog["always_tests"]) <= set(catalog["validation_units"])
    assert sum(unit["reuse"] == "candidate" for unit in catalog["validation_units"].values()) == 14
    for test in catalog["validation_units"]:
        assert test in unit_catalog.inputs_for(test, catalog)
        assert "tests/conftest.py" in unit_catalog.inputs_for(test, catalog)


@pytest.mark.parametrize("path", [
    "README.md", "operations/releases/domain_publish.py", "tests/test_flux_controller_ha.py",
    "platform/advanced-fabric/dns/kustomization.yaml", "tests/kustomize_render.py",
    "operations/releases/allocate_codename.py", "tests/test_unit_observation.py",
])
def test_observations_preserve_current_selection_and_explain_every_file(path):
    plan = impact.select([path])
    original = copy.deepcopy(plan)
    observation = unit_catalog.describe(plan)
    selected = {name for name, unit in observation["units"].items() if unit["status"] == "planned"}
    assert selected == (set(plan["tests"]) if plan["jobs"]["unit"] else set())
    assert plan == original
    assert observation["reuse_enabled"] is False
    assert all(unit["reasons"] for unit in observation["units"].values())
    assert all(unit["reuse"] == "never" for unit in observation["units"].values()
               if unit["declaration"] == "legacy-domain-selection")


def test_summary_escapes_repository_text_and_bounds_large_diffs():
    plan = impact.select(["README.md"])
    plan["paths"] = ["<script>|`unsafe`"] * 1000
    observation = unit_catalog.describe(plan)
    summary = unit_catalog.summary(observation)
    assert "<script>" not in summary
    assert "&#124;" in summary and "&#96;" in summary
    assert len(summary) < 150000
    assert len(observation["plan"]["paths"]) == 1000


@pytest.mark.parametrize("value", [["../outside"], ["/absolute"], ["a\\b"], "not-a-list", [None]])
def test_input_patterns_cannot_escape_repository(value):
    with pytest.raises(ValueError):
        unit_catalog.patterns(value)


def test_real_pytest_reports_pass_skip_xfail_and_setup_teardown_failures(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tmp_path / "conftest.py").write_text(
        "from operations.ci import unit_report\nunit_report.toolchain = lambda: {}\n"
    )
    (tests / "test_sample.py").write_text('''import pytest
def test_pass(): pass
def test_fail(): assert False
@pytest.mark.skip(reason="intentional")
def test_skip(): pass
@pytest.mark.xfail(reason="intentional")
def test_xfail(): assert False
@pytest.fixture
def bad_setup(): raise RuntimeError("setup")
def test_setup(bad_setup): pass
@pytest.fixture
def bad_teardown():
    yield
    raise RuntimeError("teardown")
def test_teardown(bad_teardown): pass
''')
    catalog = unit_catalog.load_catalog()
    catalog["validation_units"] = {}
    directory = tmp_path / "operations/ci"
    directory.mkdir(parents=True)
    (directory / "domain-catalog.yaml").write_text(yaml.safe_dump(catalog))
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    for key in ("UNIT_PLAN", "GITHUB_STEP_SUMMARY", "PYTEST_ADDOPTS"):
        env.pop(key, None)
    def run(observe):
        args = [sys.executable, "-m", "pytest", "-q"]
        if observe:
            args += ["-p", "operations.ci.unit_report", "--unit-report-dir", str(tmp_path / "report")]
        return subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, text=True)
    before, after = run(False), run(True)
    assert before.returncode == after.returncode == 1
    for output in (before.stdout, after.stdout):
        assert "1 failed, 2 passed, 1 skipped, 1 xfailed, 2 errors" in output
    report = json.loads((tmp_path / "report/units.json").read_text())
    unit = report["units"]["tests/test_sample.py"]
    assert unit["status"] == "failed"
    assert unit["outcomes"] == {"passed": 1, "failed": 3, "skipped": 1, "xfail-or-xpass": 1}
    assert set(unit["seconds"]) == {"setup", "call", "teardown"}
    assert len(unit["cases"]) == 6
    assert report["exit_code"] == 1


def test_collection_failure_and_interruption_are_not_success(tmp_path, monkeypatch):
    test = "tests/test_flux_controller_ha.py"
    plan = impact.select([test])
    monkeypatch.setenv("UNIT_PLAN", json.dumps(plan))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(unit_report, "toolchain", lambda: {})
    observer = unit_report.Observer(tmp_path, SimpleNamespace(rootpath=ROOT))
    observer.pytest_collectreport(SimpleNamespace(failed=True, nodeid=test))
    observer.cases["tests/test_github_bot_merge.py::interrupted"] = []
    observer.pytest_sessionfinish(None, 2)
    report = json.loads((tmp_path / "units.json").read_text())
    assert report["exit_code"] == 2
    assert report["units"][test]["status"] == "failed"
    assert report["units"]["tests/test_github_bot_merge.py"]["status"] == "blocked"
    assert report["units"]["tests/test_incremental_ci.py"]["status"] == "blocked"


def test_both_workflows_observe_and_upload_without_enabling_reuse():
    for file, job in (("ci.yml", "unit"), ("full-regression.yml", "regression")):
        workflow = yaml.safe_load((ROOT / ".github/workflows" / file).read_text())
        steps = workflow["jobs"][job]["steps"]
        commands = "\n".join(step.get("run", "") for step in steps)
        assert "-p operations.ci.unit_report" in commands
        assert '--junitxml="$RUNNER_TEMP/unit-observation/junit.xml"' in commands
        upload = next(step for step in steps if step.get("with", {}).get("name") == "unit-observation")
        assert upload["if"] == "always()"
        assert upload["with"]["retention-days"] == 14


def test_toolchain_records_versions_without_environment_secrets(monkeypatch):
    monkeypatch.setenv("SYNTHETIC_SECRET", "must-not-appear")
    monkeypatch.setattr(unit_report.shutil, "which", lambda name: None)
    result = unit_report.toolchain()
    assert result["python"] and result["yaml"]
    assert all(value == {"available": False} for value in result["tools"].values())
    assert "must-not-appear" not in json.dumps(result)
