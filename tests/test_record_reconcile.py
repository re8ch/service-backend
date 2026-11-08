import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "operations/github-maintenance/record_reconcile.py"
SPEC = importlib.util.spec_from_file_location("record_reconcile", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize("state", ["Failed", "Degraded", "Unknown"])
def test_non_healthy_state_routes_to_issue_backed_incident(state):
    assert MODULE.route(state) == "incident"


def test_healthy_state_routes_to_wiki():
    assert MODULE.route("Healthy") == "wiki"


def test_unrecognized_state_fails_closed():
    with pytest.raises(ValueError, match="unsupported"):
        MODULE.route("Maybe")


def test_incident_label_is_stable_and_source_specific():
    assert MODULE.incident_label("re8ch/service-backend") == MODULE.incident_label("re8ch/service-backend")
    assert MODULE.incident_label("re8ch/service-backend") != MODULE.incident_label("re8ch/identity-gitops")


def test_wiki_git_uses_separate_source_token(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "central-token")
    monkeypatch.setenv("WIKI_GH_TOKEN", "source-token")
    assert MODULE.git_env()["GH_TOKEN"] == "source-token"


def test_wiki_git_fails_closed_without_source_token(monkeypatch):
    monkeypatch.delenv("WIKI_GH_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="WIKI_GH_TOKEN"):
        MODULE.git_env()
