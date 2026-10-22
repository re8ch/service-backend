import datetime as dt
import importlib.util
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "operations/github-maintenance/weekly_summary.py"
SPEC = importlib.util.spec_from_file_location("weekly_summary", SCRIPT)
assert SPEC and SPEC.loader
SUMMARY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUMMARY)


def test_week_start_is_monday():
    assert SUMMARY.week_start(dt.date(2026, 10, 22)) == dt.date(2026, 10, 19)


def test_severity_counts_nested_values():
    alerts = [
        {"rule": {"security_severity_level": "high"}},
        {"rule": {"security_severity_level": "critical"}},
        {"rule": {"security_severity_level": "high"}},
    ]
    assert SUMMARY.severity_counts(alerts, ("rule", "security_severity_level")) == Counter(
        {"high": 2, "critical": 1}
    )


def test_render_is_quiet_and_contains_signal_counts():
    body = SUMMARY.render(
        {
            "dependabot_prs": [{}, {}],
            "dependabot_alerts": [{"security_advisory": {"severity": "high"}}],
            "codeql_alerts": [{"rule": {"security_severity_level": "critical"}}],
            "secret_alerts": [],
            "automation_issues": [{}],
            "failed_runs": [],
            "unhealthy_deployments": [],
        },
        dt.date(2026, 10, 19),
    )
    assert "Dependabot update PRs | 2" in body
    assert "CodeQL alerts | 1 (1 critical)" in body
    assert "without assignees, mentions, or\nreview requests" in body


def test_workflow_uses_maintenance_app_and_github_hosted_runner():
    workflow = (ROOT / ".github/workflows/gitops-maintenance-summary.yml").read_text()
    assert "GITOPS_MAINTENANCE_APP_ID" in workflow
    assert "GITOPS_MAINTENANCE_PRIVATE_KEY" in workflow
    assert "runs-on: ubuntu-latest" in workflow
    assert "self-hosted" not in workflow
    assert 'cron: "0 1 * * 1"' in workflow
