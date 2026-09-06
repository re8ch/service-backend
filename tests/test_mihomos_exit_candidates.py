from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_global_exit_candidates_exclude_removed_edge_and_prefer_la() -> None:
    values = yaml.safe_load(
        (ROOT / "clusters/re8ch/charts/mihomos-cluster/values.yaml").read_text()
    )
    candidates = values["exits"]["globalCandidates"]

    assert candidates == [
        {"name": "global-la", "address": "192.0.2.110"},
        {"name": "global-kl", "address": "192.0.2.245"},
    ]
    assert all(candidate["address"] != "192.0.2.79" for candidate in candidates)


def test_personal_access_no_longer_publishes_removed_edge() -> None:
    paths = (
        ROOT / "clusters/re8ch/components/personal-access/data-plane.yaml",
        ROOT / "clusters/re8ch/components/personal-access/edge-expansion.yaml",
    )
    for path in paths:
        assert "192.0.2.189" not in path.read_text()
