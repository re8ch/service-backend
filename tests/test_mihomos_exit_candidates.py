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


def test_global_destinations_use_one_ordered_exit_group() -> None:
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
    assert template.count("name: GLOBAL") == 1
    assert '"DOMAIN-SUFFIX,ghcr.io,GLOBAL"' in template
    assert '"DOMAIN-SUFFIX,pkg-containers.githubusercontent.com,GLOBAL"' in template
    assert '"DOMAIN-SUFFIX,github.com,GLOBAL"' in template
    assert template.count("$candidate.name") == 1


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
    assert config.count(ordered) == 1
    assert "DOMAIN-SUFFIX,ghcr.io,registry-egress" in config
    assert "DOMAIN-SUFFIX,pkg-containers.githubusercontent.com,registry-egress" in config
    assert "proxy-providers:" not in config
    assert "mihomo-egress-headless.egress-fabric.svc.cluster.local" not in config
    for node in ("overseas-la", "overseas-la-2", "overseas-flk"):
        assert f"server: mihomo-egress-{node}.egress-fabric.svc.cluster.local" in config
        assert f"name: mihomo-egress-{node}" in workloads
        assert f"kubernetes.io/hostname: {node}" in workloads


def test_harbor_upstreams_prefer_the_atlantic_cluster_projection() -> None:
    config = (ROOT / "clusters/re8ch/components/egress-fabric/config.yaml").read_text()
    assert "server: mihomos-global-exit-atlantic.egress-fabric.svc.cluster.local" in config
    assert "proxies: [ATLANTIC-REGISTRY, OVERSEAS-LA, OVERSEAS-LA-2, OVERSEAS-FLK]" in config
    for domain in (
        "docker.io",
        "gcr.io",
        "quay.io",
        "nvcr.io",
        "registry.k8s.io",
        "mcr.microsoft.com",
        "docker.elastic.co",
        "container-registry.oracle.com",
        "ghcr.io",
        "pkg-containers.githubusercontent.com",
    ):
        assert f"DOMAIN-SUFFIX,{domain},registry-egress" in config
    assert "DOMAIN,production.cloudflare.docker.com,registry-egress" in config


def test_mihomos_background_checks_are_bounded_and_geo_specific() -> None:
    values = yaml.safe_load(
        (ROOT / "clusters/re8ch/charts/mihomos-cluster/values.yaml").read_text()
    )
    template = (ROOT / "clusters/re8ch/charts/mihomos-cluster/templates/dataplane.yaml").read_text()
    collector = (ROOT / "clusters/re8ch/charts/mihomos-cluster/files/collector.py").read_text()
    active_config = (ROOT / "clusters/re8ch/components/egress-fabric/config.yaml").read_text()

    assert values["telemetry"]["intervalSeconds"] >= 900
    assert len(values["telemetry"]["probes"]["cn"]) == 2
    assert len(values["telemetry"]["probes"]["global"]) == 2
    assert 'route_target = {"cn": "global", "global": "cn"}.get(geo)' in collector
    assert '.get(route_target, [])' in collector
    assert "tcpSocket: {port: socks}" in template
    assert "api.github.com/rate_limit" not in template
    assert "connectivitycheck.gstatic.com" not in template
    assert "interval: 30" not in template
    assert "lazy: false" not in template
    assert "interval: 30" not in active_config
    assert "lazy: false" not in active_config
    assert "https://connect.rom.miui.com/generate_204" in template


def test_mihomos_cluster_and_private_ranges_stay_direct() -> None:
    config = (ROOT / "clusters/re8ch/components/egress-fabric/config.yaml").read_text()
    template = (ROOT / "clusters/re8ch/charts/mihomos-cluster/templates/dataplane.yaml").read_text()
    direct_ranges = (
        "IP-CIDR,10.0.0.0/8,DIRECT,no-resolve",
        "IP-CIDR,100.64.0.0/10,DIRECT,no-resolve",
        "IP-CIDR,172.16.0.0/12,DIRECT,no-resolve",
        "IP-CIDR,192.168.0.0/16,DIRECT,no-resolve",
    )
    for rule in direct_ranges:
        assert config.count(rule) == 2
        assert template.count(rule) == 2


def test_personal_access_no_longer_publishes_removed_edge() -> None:
    paths = (
        ROOT / "clusters/re8ch/components/personal-access/data-plane.yaml",
        ROOT / "clusters/re8ch/components/personal-access/edge-expansion.yaml",
    )
    for path in paths:
        assert "38.207.168.50" not in path.read_text()
