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
    assert "10.181.22.104" not in template


def test_github_egress_uses_region_pinned_global_exit() -> None:
    values = yaml.safe_load(
        (ROOT / "clusters/re8ch/charts/mihomos-cluster/values.yaml").read_text()
    )
    template = (ROOT / "clusters/re8ch/charts/mihomos-cluster/templates/dataplane.yaml").read_text()
    assert values["exits"]["githubRegion"] == "us-ca"
    assert "name: mihomos-global-exit-github" in template
    assert "topology.kubernetes.io/region: {{ $.Values.exits.githubRegion }}" in template
    assert "proxies: [GITHUB-FABRIC]" in template


def test_r640_github_downloads_do_not_bypass_mihomos() -> None:
    env = (ROOT / "operations/ci/runners/r640.env").read_text()
    docker_config = (ROOT / "operations/ci/runners/docker-config.json").read_text()
    for host in ("api.github.com", "codeload.github.com", ".githubusercontent.com"):
        assert host not in env
        assert host not in docker_config


def test_personal_access_no_longer_publishes_removed_edge() -> None:
    paths = (
        ROOT / "clusters/re8ch/components/personal-access/data-plane.yaml",
        ROOT / "clusters/re8ch/components/personal-access/edge-expansion.yaml",
    )
    for path in paths:
        assert "38.207.168.50" not in path.read_text()
