from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def definitions():
    for path in (ROOT / "plugins").glob("*.yaml"):
        data = yaml.safe_load(path.read_text())
        yield from data.get("items", [data])


def test_plugin_names_and_prefixes_are_unique():
    docs = list(definitions())
    names = [doc["metadata"]["name"] for doc in docs]
    prefixes = [doc["spec"]["publication"]["toolPrefix"] for doc in docs]
    assert len(names) == len(set(names))
    assert len(prefixes) == len(set(prefixes))


def test_public_edge_candidate_algorithm_is_denied():
    public_edge = next(doc for doc in definitions() if doc["metadata"]["name"] == "public-edge")
    assert "candidateSelection" in public_edge["spec"]["policy"]["deniedFields"]


def test_every_plugin_has_independent_identity():
    identities = [doc["spec"]["identityRef"] for doc in definitions()]
    assert len(identities) == len(set(identities))
