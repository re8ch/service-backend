import importlib.util
import io
from pathlib import Path
import pytest
import sys
import urllib.error
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


def test_normal_publication_does_not_require_harbor_project_management(monkeypatch, tmp_path):
    module = yaml.safe_load((ROOT / "operations/releases/active.yaml").read_text())["modules"][0]["module"]
    monkeypatch.setattr(publication, "harbor", lambda *args, **kwargs: pytest.fail("project management API used"))
    impact = type("Impact", (), {
        "changed_paths": staticmethod(lambda *args: []),
        "load_catalog": staticmethod(lambda: {"external_inputs": {}}),
        "root_affected": staticmethod(lambda *args: False),
        "matches": staticmethod(lambda *args: False),
    })
    monkeypatch.setattr(publication, "load_module", lambda *args: impact)
    published = []
    monkeypatch.setattr(publication, "publish", lambda modules, output, bootstrap=False: published.extend(modules))
    monkeypatch.setattr(sys, "argv", ["domain_publish.py", "--output", str(tmp_path), "--module", module])

    publication.main()

    assert [entry["module"] for entry in published] == [module]


def test_registry_manifest_uses_pull_scope_bearer_challenge(monkeypatch):
    class Response(io.BytesIO):
        def __init__(self, body, headers=None):
            super().__init__(body)
            self.headers = headers or {}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.close()

    calls = []
    def urlopen(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {
                "WWW-Authenticate": 'Bearer realm="https://registry.re8ch.com/service/token",service="harbor-registry",scope="repository:gitops-controlplane/platform-stalwart:pull"'}, None)
        if len(calls) == 2:
            return Response(b'{"token":"scoped-token"}')
        return Response(b'{"annotations":{"delivery.re8ch.com/revision":"main"}}', {
            "Docker-Content-Digest": "sha256:" + "4" * 64})

    monkeypatch.setenv("HARBOR_USERNAME", "robot")
    monkeypatch.setenv("HARBOR_PASSWORD", "secret")
    monkeypatch.setattr(publication.urllib.request, "urlopen", urlopen)

    status, manifest = publication.registry_manifest(
        "registry.re8ch.com/gitops-controlplane/platform-stalwart", "bora-silver")

    assert status == 200
    assert manifest["digest"] == "sha256:" + "4" * 64
    assert "scope=repository%3Agitops-controlplane%2Fplatform-stalwart%3Apull" in calls[1].full_url
    assert calls[2].headers["Authorization"] == "Bearer scoped-token"


@pytest.mark.parametrize("module", ["platform/runtime-anatomy", "platform/birfucate-metering"])
def test_local_chart_sources_are_module_local_commit_pinned_and_signed(module):
    documents = publication.bundle_documents(module)
    release = next(doc for doc in documents if doc["kind"] == "HelmRelease")
    chart_source_name = release["spec"]["chart"]["spec"]["sourceRef"]["name"]
    source = next(doc for doc in documents if doc["kind"] == "GitRepository" and doc["metadata"]["name"] == chart_source_name)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert source["spec"]["ref"] == {"commit": head}
    assert source["spec"]["verify"]["mode"] == "HEAD"
    assert source["spec"]["secretRef"] == {"name": "service-backend-github-ssh"}
    assert source["spec"]["url"] == "ssh://git@ssh.github.com:443/re8ch/service-backend.git"
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
    monkeypatch.setattr(publication, "registry_manifest", lambda repository, reference: (200, {"digest": digest, "annotations": {"delivery.re8ch.com/revision": "old"}}) if reference == first else (404, None))
    name, existing = publication.immutable_target(module, state, allocator, "registry.example/module", "current")
    assert name != first and existing is None
    assert state["modules"][module]["releases"][-1] == {
        "codename": first, "digest": digest, "quarantined": "missing-or-invalid-signature"}


def test_unverifiable_existing_tag_is_quarantined_and_never_claimed(monkeypatch):
    allocator = publication.load_module("untrusted_names", "operations/releases/allocate_codename.py")
    state = yaml.safe_load(allocator.STATE.read_text())
    first = allocator.proposed("platform/adapter", state)
    monkeypatch.setattr(publication, "registry_manifest", lambda repository, reference: (200, {"digest": "sha256:" + "3" * 64}) if reference == first else (404, None))
    name, existing = publication.immutable_target("platform/adapter", state, allocator, "registry.example/module", "current")
    assert existing is None
    assert name != first
    assert state["modules"]["platform/adapter"]["releases"][-1]["quarantined"] == "missing-or-invalid-signature"
