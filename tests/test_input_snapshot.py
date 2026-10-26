"""Reuse must preserve parser errors, consumer isolation and fresh filesystem inputs."""
import importlib.util
import os
from pathlib import Path

import pytest
import yaml

from operations.ci.input_snapshot import json_document, session_inputs, yaml_document, yaml_documents

ROOT = Path(__file__).resolve().parents[1]


def module(relative):
    spec = importlib.util.spec_from_file_location("snapshot_consumer", ROOT / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize("reader,text", [(yaml_document, "nested: [one]\n"), (json_document, '{"nested": ["one"]}')])
def test_isolated_copies_and_content_not_mtime(tmp_path, reader, text):
    path = tmp_path / "input"
    path.write_text(text)
    stat = path.stat()
    with session_inputs() as snapshot:
        reader(path)["nested"].append("mutation")
        assert reader(path) == {"nested": ["one"]}
        assert (snapshot.parsed, snapshot.reused) == (1, 1)
        path.write_text(text.replace("one", "two"))
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert reader(path) == {"nested": ["two"]}
        path.unlink()
        with pytest.raises(FileNotFoundError):
            reader(path)
        path.write_text(text)
        assert reader(path) == {"nested": ["one"]}
        assert snapshot.parsed == 3
    assert not snapshot._entries


def test_parser_mode_version_and_implementation_are_separate(tmp_path, monkeypatch):
    path = tmp_path / "input"
    path.write_text('{"answer": 42}')
    with session_inputs() as snapshot:
        assert yaml_document(path) == json_document(path) == {"answer": 42}
        assert list(yaml_documents(path)) == [{"answer": 42}]
        if hasattr(yaml, "CSafeLoader"):
            assert yaml_document(path, loader=yaml.CSafeLoader) == {"answer": 42}
        count = snapshot.parsed
        monkeypatch.setattr(yaml, "__version__", "changed-version")
        yaml_document(path)
        assert snapshot.parsed == count + 1
        assert snapshot.reused == 0


@pytest.mark.parametrize("text", ["ok: yes\n---\nbroken: [", "!!python/object/apply:builtins.eval ['1 + 1']"])
def test_failed_yaml_is_never_cached_and_keeps_partial_documents(tmp_path, text):
    path = tmp_path / "input"
    path.write_text(text)
    with session_inputs() as snapshot:
        for _ in range(2):
            actual, expected = [], []
            with pytest.raises(yaml.YAMLError) as direct:
                for value in yaml.load_all(text, Loader=yaml.SafeLoader):
                    expected.append(value)
            with pytest.raises(type(direct.value)) as cached:
                for value in yaml_documents(path):
                    actual.append(value)
            assert actual == expected
            assert str(cached.value) == str(direct.value)
        assert not snapshot._entries and snapshot.parsed == snapshot.reused == 0
        path.write_text("fixed: true")
        assert list(yaml_documents(path)) == [{"fixed": True}]
        assert snapshot.parsed == 1


def test_partial_iteration_does_not_publish_success(tmp_path):
    path = tmp_path / "input"
    path.write_text("a: 1\n---\nb: 2")
    with session_inputs() as snapshot:
        iterator = yaml_documents(path)
        assert next(iterator) == {"a": 1}
        iterator.close()
        assert not snapshot._entries
        assert list(yaml_documents(path)) == [{"a": 1}, {"b": 2}]
        assert snapshot.parsed == 1


def test_json_errors_and_session_boundaries(tmp_path):
    path = tmp_path / "input"
    path.write_text("{")
    with session_inputs() as snapshot:
        for _ in range(2):
            with pytest.raises(ValueError):
                json_document(path)
        assert not snapshot._entries
        path.write_text("null")
        assert json_document(path) is None
    with session_inputs() as fresh:
        assert json_document(path) is None
        assert fresh.parsed == 1 and fresh.reused == 0


def test_inventory_overlap_retains_scope_partial_errors_and_detects_mutations(tmp_path, monkeypatch):
    flux = module("operations/flux-lightweight/inventory.py")
    helm = module("operations/releases/helmrelease_inventory.py")
    for consumer in (flux, helm):
        monkeypatch.setattr(consumer, "ROOT", tmp_path)
    root = tmp_path / "clusters/re8ch"
    root.mkdir(parents=True)
    text = "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\nmetadata: {name: sample}\nspec: {interval: 1m}\n"
    path = root / "release.yaml"
    path.write_text(text)
    mirror = root / "domains/main-legacy"
    mirror.mkdir(parents=True)
    (mirror / "release.yaml").write_text(text)
    with session_inputs() as snapshot:
        first = flux.inventory([root])
        releases, errors = helm.declarations([root])
        assert first["resourceCount"] == 2 and len(releases) == 1 and not errors
        assert snapshot.reused >= 1
        # Projections may be mutated independently; later consumers see pristine inputs.
        first["resources"][0]["name"] = "mutated"
        assert flux.inventory([root])["resources"][0]["name"] == "sample"
        path.write_text(text + "---\nbroken: [")
        broken_flux = flux.inventory([root])
        broken_helm, errors = helm.declarations([root])
        assert broken_flux["resourceCount"] == 2 and len(broken_helm) == 1
        assert broken_flux["parseErrors"] == errors
        path.write_text(text.replace("sample", "changed"))
        added = root / "added.yaml"
        added.write_text(text)
        assert len(helm.declarations([root])[0]) == 2
        added.unlink()
        assert set(helm.declarations([root])[0]) == {"helmrelease:default:changed"}


def test_catalog_reread_changes_allocator_result(tmp_path, monkeypatch):
    allocator = module("operations/releases/allocate_codename.py")
    path = tmp_path / "catalog.yaml"
    path.write_text("domains: {one: {roots: [module], theme: first}}")
    monkeypatch.setattr(allocator, "CATALOG", path)
    state = {"modules": {}, "themes": {"first": ["one"], "second": ["two"]}, "revision_words": []}
    with session_inputs() as snapshot:
        assert allocator.proposed("module", state) == "one"
        assert allocator.proposed("module", state) == "one"
        assert snapshot.reused == 1
        path.write_text(path.read_text().replace("first", "second"))
        assert allocator.proposed("module", state) == "two"
