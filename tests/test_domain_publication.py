import importlib.util
from pathlib import Path
import pytest
import yaml
import subprocess

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


@pytest.mark.parametrize("module", ["platform/runtime-anatomy", "platform/birfucate-metering"])
def test_local_chart_sources_are_module_local_commit_pinned_and_signed(module):
    documents = publication.bundle_documents(module)
    release = next(doc for doc in documents if doc["kind"] == "HelmRelease")
    chart_source_name = release["spec"]["chart"]["spec"]["sourceRef"]["name"]
    source = next(doc for doc in documents if doc["kind"] == "GitRepository" and doc["metadata"]["name"] == chart_source_name)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert source["spec"]["ref"] == {"commit": head}
    assert source["spec"]["verify"]["mode"] == "HEAD"
    assert source["spec"]["secretRef"] == {"name": "service-backend-github-app-auth"}
    assert release["spec"]["chart"]["spec"]["sourceRef"]["name"] == source["metadata"]["name"]
    assert source["metadata"]["name"] != "service-backend"


def test_global_ci_change_does_not_republish_all_modules():
    entries = [{"module": "platform/adapter"}, {"module": "platform/runtime-anatomy"}]
    assert publication.affected_modules(entries, ["operations/ci/domain-catalog.yaml", ".github/workflows/ci.yml"]) == []
    assert publication.affected_modules(entries, ["platform/adapter/release.yaml"]) == [entries[0]]
    assert publication.affected_modules(entries, ["clusters/re8ch/charts/runtime-anatomy/Chart.yaml"]) == [entries[1]]


def test_stale_verified_tag_is_preserved_not_overwritten(monkeypatch):
    allocator = publication.load_module("stale_names", "operations/releases/allocate_codename.py")
    state = yaml.safe_load(allocator.STATE.read_text())
    module = "platform/adapter"
    first = allocator.proposed(module, state)
    digest = "sha256:" + "2" * 64
    monkeypatch.setattr(publication, "harbor", lambda method, path: (200, {"digest": digest, "annotations": {"delivery.re8ch.com/revision": "old"}}) if path.endswith("/" + first) else (404, None))
    calls = []
    monkeypatch.setattr(publication.subprocess, "run", lambda argv, **kwargs: calls.append(argv))
    name, existing = publication.immutable_target(module, state, allocator, "registry.example/module", "current")
    assert name != first and existing is None
    assert state["modules"][module]["releases"][-1] == {"codename": first, "digest": digest}
    assert len(calls) == 1 and calls[0][:2] == ["cosign", "verify"]
    assert calls[0][-1] == "registry.example/module@" + digest


def test_unverifiable_existing_tag_is_quarantined_and_never_claimed(monkeypatch):
    allocator = publication.load_module("untrusted_names", "operations/releases/allocate_codename.py")
    state = yaml.safe_load(allocator.STATE.read_text())
    first = allocator.proposed("platform/adapter", state)
    monkeypatch.setattr(publication, "harbor", lambda method, path: (200, {"digest": "sha256:" + "3" * 64}) if path.endswith("/" + first) else (404, None))
    def reject(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv)
    monkeypatch.setattr(publication.subprocess, "run", reject)
    name, existing = publication.immutable_target("platform/adapter", state, allocator, "registry.example/module", "current")
    assert existing is None
    assert name != first
    assert state["modules"]["platform/adapter"]["releases"][-1]["quarantined"] == "missing-or-invalid-signature"
