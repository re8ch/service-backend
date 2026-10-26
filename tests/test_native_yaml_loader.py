"""Keep the native parser's safety and data semantics aligned with SafeLoader."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    "operations/releases/helmrelease_inventory.py",
    "operations/flux-lightweight/inventory.py",
    "operations/ci/impact.py",
    "operations/releases/compile_main_domain_ssa.py",
    "operations/releases/unified_bundle.py",
    "operations/workspace-controller/workspace.py",
    "operations/flux-federation/validate.py",
    "tests/test_domain_reconcilers.py",
    "tests/test_main_domain_ssa.py",
    "tests/test_flux_controller_ha.py",
    "tests/test_flux_fast_reconcile.py",
    "tests/test_flux_image_controller_retirement.py",
    "tests/test_tenant_infrastructure_gitops.py",
    "tests/test_release_codenames.py",
)


@pytest.mark.parametrize("path", TARGETS)
@pytest.mark.parametrize("native", [False, True])
def test_each_consumer_selects_safe_native_or_python_fallback(path, native):
    parser = ast.parse((ROOT / path).read_text())
    assignment = next(node for node in parser.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "YAML_SAFE_LOADER"
                              for target in node.targets))
    api = {"SafeLoader": yaml.SafeLoader}
    if native:
        if not hasattr(yaml, "CSafeLoader"):
            pytest.skip("LibYAML is not installed")
        api["CSafeLoader"] = yaml.CSafeLoader
    namespace = {"yaml": SimpleNamespace(**api)}
    exec(compile(ast.Module(body=[assignment], type_ignores=[]), path, "exec"), namespace)
    loader = namespace["YAML_SAFE_LOADER"]
    assert loader is api.get("CSafeLoader", yaml.SafeLoader)
    # No Python object constructors, including in fallback environments.
    with pytest.raises(yaml.YAMLError):
        yaml.load("!!python/object/apply:builtins.eval ['1 + 1']", Loader=loader)
    calls = [node for node in ast.walk(parser) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and isinstance(node.func.value, ast.Name) and node.func.value.id == "yaml"]
    assert not any(call.func.attr in {"safe_load", "safe_load_all"} for call in calls)
    for call in calls:
        if call.func.attr in {"load", "load_all"}:
            assert any(arg.arg == "Loader" and isinstance(arg.value, ast.Name)
                       and arg.value.id == "YAML_SAFE_LOADER" for arg in call.keywords)


@pytest.mark.skipif(not hasattr(yaml, "CSafeLoader"), reason="LibYAML is not installed")
@pytest.mark.parametrize("source", [
    "",
    "a: true\nb: yes\nc: 'yes'\nd: null\ne: 012\nf: '012'\n",
    "date: 2026-10-27\ntime: 2026-10-27T00:00:00Z\n",
    "message: |\n  中文\n  multiline\nblob: !!binary aGVsbG8=\n",
    "base: &base {name: app, replicas: 2}\ncopy: {<<: *base, replicas: 3}\n",
    "duplicate: first\nduplicate: second\n",
    "---\napiVersion: v1\nkind: ConfigMap\n---\nnull\n---\nitems: []\n",
])
def test_native_and_python_preserve_values_types_and_serialization(source):
    python = list(yaml.load_all(source, Loader=yaml.SafeLoader))
    native = list(yaml.load_all(source, Loader=yaml.CSafeLoader))
    assert native == python
    assert yaml.safe_dump_all(native, sort_keys=False) == yaml.safe_dump_all(python, sort_keys=False)


@pytest.mark.skipif(not hasattr(yaml, "CSafeLoader"), reason="LibYAML is not installed")
@pytest.mark.parametrize("source", ["key: [unterminated", "key: *missing", "!unknown value", "a: 1\n\tbad: 2"])
def test_both_parsers_reject_invalid_documents(source):
    for loader in (yaml.SafeLoader, yaml.CSafeLoader):
        with pytest.raises(yaml.YAMLError):
            list(yaml.load_all(source, Loader=loader))


@pytest.mark.skipif(not hasattr(yaml, "CSafeLoader"), reason="LibYAML is not installed")
@pytest.mark.parametrize("path", [
    "operations/releases/helmrelease-inventory.yaml",
    "operations/releases/helmrelease-classifications.yaml",
    "operations/ci/domain-catalog.yaml",
    "clusters/re8ch/flux-federation/controllers.yaml",
    "clusters/re8ch/domains/identity-access/resources.yaml",
])
def test_reviewed_gitops_documents_have_identical_data_and_output(path):
    text = (ROOT / path).read_text()
    python = list(yaml.load_all(text, Loader=yaml.SafeLoader))
    native = list(yaml.load_all(text, Loader=yaml.CSafeLoader))
    assert native == python
    assert yaml.safe_dump_all(native, sort_keys=False) == yaml.safe_dump_all(python, sort_keys=False)
