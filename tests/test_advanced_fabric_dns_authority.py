from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def documents(path):
    return [item for item in yaml.safe_load_all(path.read_text()) if item]


def test_advanced_fabric_is_the_fixed_cluster_dns_authority():
    resources = documents(ROOT / "platform/advanced-fabric/release.yaml")
    release = next(item for item in resources if item["kind"] == "HelmRelease")
    dns = release["spec"]["values"]["advancedFabric"]["dns"]

    assert dns["enabled"] is True
    assert dns["replicas"] == 3
    assert dns["pdbMinAvailable"] == 2
    assert dns["testNodeSelector"] == {"kubernetes.io/hostname": "restricted-node-1eb0570f74"}
    hostnames = dns["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][0]
    assert hostnames == {
        "key": "kubernetes.io/hostname",
        "operator": "In",
        "values": ["restricted-node-1eb0570f74", "restricted-node-296f36f2fb", "restricted-node-6dc3f140f9"],
    }
    anti_affinity = dns["affinity"]["podAntiAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][0]
    assert anti_affinity["topologyKey"] == "kubernetes.io/hostname"
    assert anti_affinity["labelSelector"]["matchLabels"] == {
        "app.kubernetes.io/name": "advanced-fabric-doh"
    }
    assert dns["service"] == {"name": "advanced-fabric-dns", "clusterIP": "192.0.2.42"}
    assert dns["conditionalForwarders"]["zt.re8ch.com"] == [
        "192.0.2.67", "192.0.2.79", "192.0.2.203"
    ]
    assert release["spec"]["upgrade"]["preserveValues"] is False
    assert release["spec"]["test"] == {"enable": True, "ignoreFailures": False}
    assert release["spec"]["values"]["advancedFabric"]["networkQuality"]["victoriaMetricsServiceScrape"] is False

    patches = release["spec"]["postRenderers"][0]["kustomize"]["patches"]
    authoritative_dns_patch = next(
        item for item in patches if item["target"]["name"] == "advanced-fabric-dns"
    )
    assert "trafficDistribution" in authoritative_dns_patch["patch"]
    assert "PreferSameNode" in authoritative_dns_patch["patch"]


def test_every_managed_server_uses_fixed_cluster_dns():
    configs = sorted((ROOT / "clusters/re8ch/components/ops").glob("*k3s-server-config.yaml"))
    assert len(configs) == 3
    for config in configs:
        rendered = yaml.safe_load(config.read_text())
        assert rendered["cluster-dns"] == "192.0.2.42"
        assert "coredns" in rendered["disable"]


def test_legacy_cluster_dns_manifests_are_absent():
    forbidden = [
        ROOT / "clusters/re8ch/components/ops/coredns-availability-policy.yaml",
        ROOT / "clusters/re8ch/components/ops/coredns-availability.patch.yaml",
        ROOT / "clusters/re8ch/components/cilium/node-local-dns-cache.yaml",
        ROOT / "clusters/re8ch/components/cilium/node-local-dns-lrp.yaml",
        ROOT / "clusters/re8ch/dns/zt-re8ch-coredns/coredns-custom-configmap.yaml",
    ]
    assert not any(path.exists() for path in forbidden)


def test_observability_scrapes_advanced_fabric_dns():
    resources = documents(ROOT / "platform/observability/production/scrapes.yaml")
    scrape = next(item for item in resources if item["metadata"]["name"] == "advanced-fabric-dns")
    assert scrape["spec"]["selector"]["matchLabels"]["app.kubernetes.io/name"] == "advanced-fabric-dns"
    assert scrape["spec"]["endpoints"][0]["port"] == "metrics"
