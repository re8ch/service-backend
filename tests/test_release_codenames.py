import importlib.util
from pathlib import Path

import yaml

# LibYAML keeps SafeLoader constructors; pure Python remains a supported fallback.
YAML_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("names", ROOT / "operations/releases/allocate_codename.py")
names = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(names)


def test_each_deployable_module_has_an_independent_codename_stream():
    state = yaml.load((ROOT / "operations/releases/codenames.yaml").read_text(), Loader=YAML_SAFE_LOADER)
    modules = names.module_themes()
    assert modules
    assert all(names.proposed(module, state) for module in modules)


def test_revision_suffix_is_a_word_not_a_number():
    state = yaml.load((ROOT / "operations/releases/codenames.yaml").read_text(), Loader=YAML_SAFE_LOADER)
    module = "platform/advanced-fabric"
    size = len(state["themes"][names.module_themes()[module]])
    state["modules"][module] = {"releases": [{"codename": "used"}] * size}
    assert names.proposed(module, state).endswith("-amber")


def test_revision_vocabulary_has_capacity_after_all_previous_words():
    state = yaml.load((ROOT / "operations/releases/codenames.yaml").read_text(), Loader=YAML_SAFE_LOADER)
    module = "platform/birfucate-metering"
    size = len(state["themes"][names.module_themes()[module]])
    previously_reviewed_words = len(state["revision_words"]) - 12
    state["modules"][module] = {
        "releases": [{"codename": "used"}] * size * (previously_reviewed_words + 1)
    }
    assert names.proposed(module, state).endswith("-amethyst")
