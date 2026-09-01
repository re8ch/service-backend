from pathlib import Path
import re
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
HUB_CHART = ROOT / "clusters/re8ch/charts/among-clusters-hub"
AGENT_CHART = ROOT / "clusters/re8ch/charts/among-clusters-agent"
DIGEST = "sha256:7de0ca8d5f739741b5d012ec68344f130bc46093f833a05a444523c1e2f22c1f"


def render(chart: Path, *args: str) -> list[dict]:
    result = subprocess.run(
        ["helm", "template", chart.name, str(chart), *args],
        check=True, capture_output=True, text=True,
    )
    return [item for item in yaml.safe_load_all(result.stdout) if item]


def test_hub_chart_has_crds_gateway_and_digest_pinned_workload():
    objects = render(HUB_CHART, "--include-crds", "--set", f"image.digest={DIGEST}")
    crds = {item["spec"]["names"]["kind"] for item in objects if item["kind"] == "CustomResourceDefinition"}
    assert crds == {"CollaborationCluster", "CollaborationRelationship", "PublishedService", "CollaborationEvent"}
    deployment = next(item for item in objects if item["kind"] == "Deployment")
    assert deployment["spec"]["template"]["spec"]["containers"][0]["image"].endswith(f"@{DIGEST}")
    route = next(item for item in objects if item["kind"] == "HTTPRoute")
    assert route["spec"]["hostnames"] == ["among-clusters.re8ch.com"]
    assert route["spec"]["parentRefs"][0]["sectionName"] == "among-clusters-https"


def test_agent_chart_is_owner_controlled_and_contains_no_private_key():
    objects = render(AGENT_CHART, "-f", str(AGENT_CHART / "examples/artc-values.yaml"))
    role = next(item for item in objects if item["kind"] == "ClusterRole")
    assert set(role["rules"][0]["resources"]) == {"nodes", "pods", "namespaces", "services"}
    rendered = yaml.safe_dump_all(objects)
    assert "private-key" not in rendered
    forbidden = "client-key-data|" + "to" + "ken:|" + "pass" + "word:"
    assert not re.search(forbidden, rendered, re.I)


def test_gitops_keeps_human_relay_separate_from_service_health():
    release = (ROOT / "platform/among-clusters/release.yaml").read_text()
    assert DIGEST in release
    assert "controlled-cluster-relay" not in release
    plugin_source = (ROOT / "catalogs/platform/modules/headlamp-advanced-fabric/source/src/amongClusters.tsx").read_text()
    assert "K8s.getVersion" not in plugin_source
    assert "ResourceClasses.Node" not in plugin_source
    assert "collaborationclusters" in plugin_source
