from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "clusters/re8ch/charts/controlled-cluster-connection"


def render(values: str) -> list[dict]:
    result = subprocess.run(
        ["helm", "template", "connection", str(CHART), "--namespace", "cluster-access", "-f", str(CHART / values)],
        check=True,
        capture_output=True,
        text=True,
    )
    return [item for item in yaml.safe_load_all(result.stdout) if item]


def test_chart_defaults_are_fail_closed():
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
    assert chart["type"] == "application"
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    assert values["accessGrants"] == []
    assert values["sharedServices"] == []
    assert values["contract"]["dataPlaneSharing"] == "prohibited"


def test_artc_renders_owner_admin_without_credentials():
    objects = render("examples/artc-values.yaml")
    binding = next(item for item in objects if item["kind"] == "ClusterRoleBinding")
    assert binding["roleRef"]["name"] == "cluster-admin"
    assert {subject["name"] for subject in binding["subjects"]} == {
        "oidc:trendstudiony@gmail.com",
        "oidc:artchais-hedyzhang",
    }
    rendered = yaml.safe_dump_all(objects)
    forbidden_fields = (
        "client-key-data",
        "client-certificate-data",
        "token" + ":",
        "password" + ":",
    )
    for forbidden in forbidden_fields:
        assert forbidden not in rendered


def test_qwen_renders_separate_owner_contract():
    objects = render("examples/qwen-values.yaml")
    contract = next(item for item in objects if item["kind"] == "ConfigMap")
    assert 'name: "qwen"' in contract["data"]["contract.yaml"]
    binding = next(item for item in objects if item["kind"] == "ClusterRoleBinding")
    assert binding["metadata"]["labels"]["access.re8ch.com/owner"] == "qwen"
    contract_json = yaml.safe_load(contract["data"]["contract.json"])
    assert contract_json["spec"]["sharedServices"][0]["direction"] == "owner-to-consumer"
