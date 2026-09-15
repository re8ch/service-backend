import importlib.util
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("names", ROOT / "operations/releases/allocate_codename.py")
names = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(names)


def test_each_deployable_module_has_an_independent_codename_stream():
    state = yaml.safe_load((ROOT / "operations/releases/codenames.yaml").read_text())
    modules = names.module_themes()
    assert modules
    assert all(names.proposed(module, state) for module in modules)


def test_revision_suffix_is_a_word_not_a_number():
    state = yaml.safe_load((ROOT / "operations/releases/codenames.yaml").read_text())
    module = "platform/advanced-fabric"
    size = len(state["themes"][names.module_themes()[module]])
    state["modules"][module] = {"releases": [{"codename": "used"}] * size}
    assert names.proposed(module, state).endswith("-amber")
