from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_mihomos_consumers_discover_ready_exit_services() -> None:
    values = yaml.safe_load(
        (ROOT / "clusters/re8ch/charts/mihomos-cluster/values.yaml").read_text()
    )
    template = (ROOT / "clusters/re8ch/charts/mihomos-cluster/templates/dataplane.yaml").read_text()
    assert "cnCandidates" not in values["exits"]
    assert len(values["exits"]["globalCandidates"]) == 3
    assert "mihomos-global-exit-{{ .node }}.{{ $.Release.Namespace }}.svc.cluster.local" in template
    assert "mihomos-cn-exit.{{ .Release.Namespace }}.svc.cluster.local" in template
    assert "publishNotReadyAddresses: false" in template
    assert "10.181.22.104" not in template


def test_github_and_ghcr_use_explicit_ordered_global_exits() -> None:
    values = yaml.safe_load(
        (ROOT / "clusters/re8ch/charts/mihomos-cluster/values.yaml").read_text()
    )
    template = (ROOT / "clusters/re8ch/charts/mihomos-cluster/templates/dataplane.yaml").read_text()
    candidates = values["exits"]["globalCandidates"]
    assert candidates == [
        {"name": "OVERSEAS-LA", "node": "overseas-la"},
        {"name": "OVERSEAS-LA-2", "node": "overseas-la-2"},
        {"name": "OVERSEAS-FLK", "node": "overseas-flk"},
    ]
    assert "metadata: {name: mihomos-global-exit-{{ .node }}" in template
    assert "kubernetes.io/hostname: {{ .node }}" in template
    assert "topology.kubernetes.io/region" not in template
    assert "name: GHCR" in template
    assert 'url: "https://ghcr.io/v2/"' in template
    assert '"DOMAIN-SUFFIX,ghcr.io,GHCR"' in template
    assert '"DOMAIN-SUFFIX,pkg-containers.githubusercontent.com,GHCR"' in template
    assert template.count("$candidate.name") == 3


def test_r640_github_downloads_do_not_bypass_mihomos() -> None:
    env = (ROOT / "operations/ci/runners/r640.env").read_text()
    docker_config = (ROOT / "operations/ci/runners/docker-config.json").read_text()
    for host in ("api.github.com", "codeload.github.com", ".githubusercontent.com"):
        assert host not in env
        assert host not in docker_config


def test_active_egress_fabric_uses_three_explicit_ordered_exits() -> None:
    config = (ROOT / "clusters/re8ch/components/egress-fabric/config.yaml").read_text()
    workloads = (ROOT / "clusters/re8ch/components/egress-fabric/workloads.yaml").read_text()
    ordered = "proxies: [OVERSEAS-LA, OVERSEAS-LA-2, OVERSEAS-FLK]"
    assert config.count(ordered) == 3
    assert "DOMAIN-SUFFIX,ghcr.io,ghcr-egress" in config
    assert "DOMAIN-SUFFIX,pkg-containers.githubusercontent.com,ghcr-egress" in config
    assert "mihomo-egress-headless.egress-fabric.svc.cluster.local" not in config
    for node in ("overseas-la", "overseas-la-2", "overseas-flk"):
        assert f"server: mihomo-egress-{node}.egress-fabric.svc.cluster.local" in config
        assert f"name: mihomo-egress-{node}" in workloads
        assert f"kubernetes.io/hostname: {node}" in workloads


def test_personal_access_no_longer_publishes_removed_edge() -> None:
    paths = (
        ROOT / "clusters/re8ch/components/personal-access/data-plane.yaml",
        ROOT / "clusters/re8ch/components/personal-access/edge-expansion.yaml",
    )
    for path in paths:
        assert "38.207.168.50" not in path.read_text()
