"""Desired-state guards for the activated private domain OCI inventories."""
from pathlib import Path
import importlib.util
import copy
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_active_sources_are_private_verified_and_digest_locked():
    entries = yaml.safe_load((ROOT / "operations/releases/active.yaml").read_text())["modules"]
    root = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    children = list(yaml.safe_load_all((ROOT / "platform/observability-source/source.yaml").read_text()))
    names = set()
    for entry in entries:
        module = entry["module"]
        slug = module.replace("/", "-")
        release = yaml.safe_load((ROOT / f"releases/{slug}.yaml").read_text())
        documents = list(yaml.safe_load_all((ROOT / f"clusters/re8ch/domain-sources/{slug}.yaml").read_text()))
        source = documents[0]
        assert source["kind"] == "OCIRepository"
        assert source["metadata"]["name"] not in names
        names.add(source["metadata"]["name"])
        assert source["spec"]["secretRef"] == {"name": "gitops-oci-reader"}
        assert source["spec"]["ref"] == {"digest": release["bundle"]["digest"]}
        assert source["spec"]["url"] == release["bundle"]["url"]
        assert source["spec"]["verify"]["provider"] == "cosign"
        assert source["spec"]["verify"]["matchOIDCIdentity"] == [{
            "issuer": "^" + re.escape(release["bundle"]["issuer"]) + "$",
            "subject": "^" + re.escape(release["bundle"]["identity"]) + "$",
        }]
        trusted = yaml.safe_load((ROOT / "operations/releases/publishers.yaml").read_text())["modules"][module]
        assert trusted == {"issuer": release["bundle"]["issuer"], "identity": release["bundle"]["identity"]}
        reconciliation = next(doc for doc in children if doc["metadata"]["name"] == entry["name"]) if entry.get("name") else documents[1]
        assert reconciliation["spec"]["sourceRef"] == {"kind": "OCIRepository", "name": source["metadata"]["name"]}
        assert reconciliation["spec"]["path"] == "./"
        assert reconciliation["spec"]["decryption"] == {"provider": "sops", "secretRef": {"name": "sops-age"}}
        assert reconciliation["spec"]["prune"] is False
        if entry.get("name"):
            assert reconciliation["spec"].get("suspend", False) == entry["suspend"]
            assert reconciliation["spec"]["timeout"] == entry["timeout"]
        else:
            assert reconciliation["spec"]["wait"] is False
            assert all((ROOT / "clusters/re8ch" / ref).resolve() != ROOT / module for ref in root["resources"])
    if entries:
        assert "domain-sources" in root["resources"]
        assert "flux-system/gitops-oci-reader.sops.yaml" in root["resources"]


def test_promotion_keeps_source_auth_verification_and_reconciliation_specs(tmp_path):
    entries = yaml.safe_load((ROOT / "operations/releases/active.yaml").read_text())["modules"]
    if not entries:
        return
    spec = importlib.util.spec_from_file_location("promotion_guard", ROOT / "operations/releases/promote.py")
    promotion = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(promotion)
    module = next(entry["module"] for entry in entries if not entry.get("name"))
    filename = module.replace("/", "-") + ".yaml"
    original = yaml.safe_load((ROOT / "releases" / filename).read_text())
    release = copy.deepcopy(original)
    allocator_spec = importlib.util.spec_from_file_location("guard_names", ROOT / "operations/releases/allocate_codename.py")
    allocator = importlib.util.module_from_spec(allocator_spec)
    allocator_spec.loader.exec_module(allocator)
    state = yaml.safe_load((ROOT / "operations/releases/codenames.yaml").read_text())
    release["codename"] = allocator.proposed(module, state)
    release["bundle"]["digest"] = "sha256:" + "1" * 64
    state["modules"][module]["releases"].append({"codename": release["codename"], "digest": release["bundle"]["digest"]})
    (tmp_path / "releases").mkdir()
    (tmp_path / "releases" / filename).write_text(yaml.safe_dump(release))
    (tmp_path / "codenames.yaml").write_text(yaml.safe_dump(state))
    changed = dict(promotion.changes(tmp_path))
    source_path = "clusters/re8ch/domain-sources/" + filename
    assert set(changed) == {source_path, "releases/" + filename, "operations/releases/codenames.yaml"}
    before = list(yaml.safe_load_all((ROOT / source_path).read_text()))
    after = list(yaml.safe_load_all(changed[source_path]))
    before[0]["spec"]["ref"] = after[0]["spec"]["ref"]
    for document in before:
        document["metadata"]["annotations"]["delivery.re8ch.com/codename"] = release["codename"]
    assert before == after


def test_promotion_reuses_an_equivalent_open_pr(monkeypatch):
    spec = importlib.util.spec_from_file_location("promotion_guard", ROOT / "operations/releases/promote.py")
    promotion = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(promotion)
    additions = [("releases/platform-adapter.yaml", "exact digest lock")]
    calls = []

    def fake_api(path, body=None):
        calls.append(path)
        if path.startswith("repos/re8ch/service-backend/pulls?"):
            return [{"html_url": "https://github.com/re8ch/service-backend/pull/999", "head": {"ref": "codex/promote-domain-42"}}]
        assert path == "repos/re8ch/service-backend/contents/releases/platform-adapter.yaml?ref=codex/promote-domain-42"
        import base64
        return {"content": base64.b64encode(b"exact digest lock").decode()}

    monkeypatch.setattr(promotion, "api", fake_api)
    assert promotion.existing_promotion(additions)["html_url"].endswith("/999")
    assert len(calls) == 2


def test_promotion_does_not_reuse_a_different_open_pr(monkeypatch):
    spec = importlib.util.spec_from_file_location("promotion_guard", ROOT / "operations/releases/promote.py")
    promotion = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(promotion)

    def fake_api(path, body=None):
        if path.startswith("repos/re8ch/service-backend/pulls?"):
            return [{"html_url": "https://github.com/re8ch/service-backend/pull/999", "head": {"ref": "codex/promote-domain-42"}}]
        import base64
        return {"content": base64.b64encode(b"a different digest lock").decode()}

    monkeypatch.setattr(promotion, "api", fake_api)
    assert promotion.existing_promotion([("releases/platform-adapter.yaml", "exact digest lock")]) is None


def test_local_chart_edits_select_only_their_deployable_owner():
    spec = importlib.util.spec_from_file_location("chart_impact_guard", ROOT / "operations/ci/impact.py")
    impact = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(impact)
    plan = impact.select(["clusters/re8ch/charts/runtime-anatomy/templates/deployment.yaml"])
    assert plan["full"] is False
    assert plan["roots"] == ["platform/runtime-anatomy"]
