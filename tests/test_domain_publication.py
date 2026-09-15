import importlib.util
from pathlib import Path
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("publication", ROOT / "operations/releases/domain_publish.py")
publication = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publication)


def test_bootstrap_only_selects_current_direct_roots_and_existing_children():
    modules = publication.initial_modules()
    ids = [entry["module"] for entry in modules]
    assert len(ids) == len(set(ids))
    assert "platform/observability-source" not in ids
    assert "platform/scheduling-fabric/resources" not in ids
    production = next(entry for entry in modules if entry.get("name") == "observability-production")
    assert production["suspend"] is True


def test_retired_workloads_are_not_resurrected(monkeypatch):
    retired = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "cloud-platform-tool", "namespace": "ai-operations"}}
    active = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "example"}}
    monkeypatch.setattr(publication.subprocess, "check_output", lambda *args, **kwargs: yaml.safe_dump_all([retired, active]).encode())
    assert publication.bundle_documents("platform/cloud-platform") == [active]


def test_public_harbor_project_is_rejected_before_robot_creation(monkeypatch, tmp_path):
    calls = []
    def request(method, path, body=None):
        calls.append(method)
        return 200, {"metadata": {"public": "true"}}
    monkeypatch.setattr(publication, "harbor", request)
    with pytest.raises(RuntimeError, match="private"):
        publication.provision_reader(tmp_path)
    assert calls == ["GET"]
