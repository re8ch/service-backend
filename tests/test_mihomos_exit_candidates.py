from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_mihomos_consumers_discover_ready_exit_services() -> None:
    values = yaml.safe_load(
        (ROOT / "clusters/re8ch/charts/mihomos-cluster/values.yaml").read_text()
    )
    template = (ROOT / "clusters/re8ch/charts/mihomos-cluster/templates/dataplane.yaml").read_text()
    assert "globalCandidates" not in values["exits"]
    assert "cnCandidates" not in values["exits"]
    assert "mihomos-global-exit.{{ .Release.Namespace }}.svc.cluster.local" in template
    assert "mihomos-cn-exit.{{ .Release.Namespace }}.svc.cluster.local" in template
    assert "publishNotReadyAddresses" not in template
    assert "192.0.2.110" not in template


def test_personal_access_no_longer_publishes_removed_edge() -> None:
    paths = (
        ROOT / "clusters/re8ch/components/personal-access/data-plane.yaml",
        ROOT / "clusters/re8ch/components/personal-access/edge-expansion.yaml",
    )
    for path in paths:
        assert "192.0.2.189" not in path.read_text()
