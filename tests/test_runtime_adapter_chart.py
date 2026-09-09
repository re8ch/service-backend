import pathlib
import subprocess

import yaml


ROOT = pathlib.Path(__file__).parents[1]
CHART = ROOT / "clusters/re8ch/charts/runtime-adapter"


def render(*args):
    command = ["helm", "template", "runtime-adapter", str(CHART), "--namespace", "runtime-adapter", *args]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return list(yaml.safe_load_all(result.stdout))


def test_default_release_is_private_and_has_real_openai_provider():
    objects = render()
    assert not any(item.get("kind") == "HTTPRoute" for item in objects)
    config = next(item for item in objects if item.get("kind") == "ConfigMap")
    providers = config["data"]["providers.json"]
    assert "litellm-openai" in providers
    assert "external-serverless-simulator" not in providers


def test_chart_grants_read_only_fabric_discovery():
    objects = render()
    role = next(item for item in objects if item.get("kind") == "ClusterRole")
    verbs = {verb for rule in role["rules"] for verb in rule["verbs"]}
    assert verbs == {"get", "list", "watch"}
    assert any("advancedfabrics" in rule["resources"] for rule in role["rules"])
    assert any("endpointslices" in rule["resources"] for rule in role["rules"])


def test_public_edge_requires_explicit_hostname():
    result = subprocess.run(
        ["helm", "template", "runtime-adapter", str(CHART), "--namespace", "runtime-adapter",
         "--set", "publicEdge.enabled=true"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "publicEdge.hostname is required" in result.stderr


def test_public_edge_route_can_be_rendered_explicitly():
    objects = render("--set", "publicEdge.enabled=true", "--set", "publicEdge.hostname=runtime.example.com")
    route = next(item for item in objects if item.get("kind") == "HTTPRoute")
    assert route["spec"]["hostnames"] == ["runtime.example.com"]

