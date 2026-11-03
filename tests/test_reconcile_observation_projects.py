import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "operations/github-maintenance/reconcile_observation_projects.py"
SPEC = importlib.util.spec_from_file_location("reconcile_observation_projects", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def declaration():
    return {
        "kind": "GitOpsObservationProjectSet",
        "fields": [{"name": "Related Logs", "dataType": "TEXT"}],
        "projects": [{"owner": "example", "repository": "example/gitops", "title": "GitOps Observations", "shortDescription": "bounded evidence"}],
    }


def test_validate_requires_same_owner():
    document = declaration()
    document["projects"][0]["repository"] = "other/gitops"
    with pytest.raises(ValueError, match="owner differ"):
        MODULE.validate_declaration(document)


def test_dry_run_reports_create(monkeypatch):
    monkeypatch.setattr(MODULE, "projects", lambda owner: [])
    assert MODULE.reconcile(declaration(), apply=False) == [{"repository": "example/gitops", "project": "GitOps Observations", "action": "create"}]


def test_apply_creates_fields_and_links(monkeypatch):
    calls = []
    monkeypatch.setattr(MODULE, "projects", lambda owner: [])
    monkeypatch.setattr(MODULE, "create_project", lambda spec: {"id": "P1", "number": 4, "title": spec["title"], "url": "https://example/project"})
    monkeypatch.setattr(MODULE, "update_project", lambda project_id, spec: calls.append(("update", project_id)))
    monkeypatch.setattr(MODULE, "link_project", lambda project_id, repository: calls.append(("link", repository)))
    monkeypatch.setattr(MODULE, "project_fields", lambda project_id: [])
    monkeypatch.setattr(MODULE, "create_field", lambda project_id, field: calls.append(("field", field["name"])))
    result = MODULE.reconcile(declaration(), apply=True)
    assert result[0]["action"] == "created"
    assert ("link", "example/gitops") not in calls
    assert ("field", "Related Logs") in calls
