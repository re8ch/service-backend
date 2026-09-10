import pathlib
import subprocess

import yaml


ROOT = pathlib.Path(__file__).parents[1]
CHART = ROOT / "clusters/re8ch/charts/adapter"
RELEASE = ROOT / "platform/adapter/release.yaml"


def render(*args):
    command = ["helm", "template", "adapter", str(CHART), "--namespace", "adapter", *args]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return list(yaml.safe_load_all(result.stdout))


def test_default_release_is_private_and_has_real_openai_provider():
    objects = render()
    assert not any(item.get("kind") == "HTTPRoute" for item in objects)
    config = next(item for item in objects if item.get("kind") == "ConfigMap")
    providers = config["data"]["providers.json"]
    assert "litellm-openai" in providers
    assert "external-serverless-simulator" not in providers
    gateway = next(item for item in objects if item.get("kind") == "Deployment" and item["metadata"]["name"] == "adapter")
    env = gateway["spec"]["template"]["spec"]["containers"][0]["env"]
    assert next(item["value"] for item in env if item["name"] == "MAX_REQUEST_BYTES") == "16777216"


def test_chart_has_adapter_artifact_hub_identity():
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
    assert chart["name"] == "adapter"
    assert chart["annotations"]["artifacthub.io/license"] == "Apache-2.0"
    assert "artifacthub.io/changes" in chart["annotations"]


def test_cluster_consumes_digest_pinned_oci_chart():
    objects = list(yaml.safe_load_all(RELEASE.read_text()))
    source = next(item for item in objects if item["kind"] == "OCIRepository")
    release = next(item for item in objects if item["kind"] == "HelmRelease")
    assert source["spec"]["url"] == "oci://registry.re8ch.com/helm/adapter"
    assert source["spec"]["ref"]["digest"].startswith("sha256:")
    assert release["spec"]["chartRef"] == {"kind": "OCIRepository", "name": "adapter"}
    assert "chart" not in release["spec"]


def test_chart_grants_read_only_fabric_discovery():
    objects = render()
    role = next(item for item in objects if item.get("kind") == "ClusterRole")
    verbs = {verb for rule in role["rules"] for verb in rule["verbs"]}
    assert verbs == {"get", "list", "watch"}
    assert any("advancedfabrics" in rule["resources"] for rule in role["rules"])
    assert any("endpointslices" in rule["resources"] for rule in role["rules"])
    assert any("nucliofunctions" in rule["resources"] for rule in role["rules"])


def test_default_chart_discovers_nuclio_and_scrapes_adapter_metrics():
    objects = render()
    config = next(item for item in objects if item.get("kind") == "ConfigMap")
    assert '"kind":"nuclio"' in config["data"]["providers.json"]
    assert any(item.get("kind") == "VMServiceScrape" for item in objects)


def test_real_spin_engine_requires_immutable_inputs_and_renders_native_command():
    failed = subprocess.run(
        ["helm", "template", "adapter", str(CHART), "--set", "spin.enabled=true"],
        capture_output=True, text=True,
    )
    assert failed.returncode != 0
    digest = "sha256:" + "a" * 64
    objects = render("--set", "spin.enabled=true", "--set-string", f"spin.image.digest={digest}",
                     "--set-string", "spin.application=harbor.example/runtime/hello@sha256:artifact")
    deployment = next(item for item in objects if item.get("kind") == "Deployment" and item["metadata"]["name"] == "adapter-spin")
    assert deployment["spec"]["template"]["spec"]["containers"][0]["args"][:3] == ["up", "--from-registry", "harbor.example/runtime/hello@sha256:artifact"]


def test_real_wasmedge_engine_uses_runtime_class():
    digest = "sha256:" + "b" * 64
    objects = render("--set", "wasmedge.enabled=true", "--set-string", f"wasmedge.image.digest={digest}")
    deployment = next(item for item in objects if item.get("kind") == "Deployment" and item["metadata"]["name"] == "adapter-wasmedge")
    assert deployment["spec"]["template"]["spec"]["runtimeClassName"] == "wasmedge"


def test_public_edge_requires_explicit_hostname():
    result = subprocess.run(
        ["helm", "template", "adapter", str(CHART), "--namespace", "adapter",
         "--set", "publicEdge.enabled=true"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "publicEdge.hostname is required" in result.stderr


def test_public_edge_route_can_be_rendered_explicitly():
    objects = render("--set", "publicEdge.enabled=true", "--set", "publicEdge.hostname=runtime.example.com")
    route = next(item for item in objects if item.get("kind") == "HTTPRoute")
    assert route["spec"]["hostnames"] == ["runtime.example.com"]
