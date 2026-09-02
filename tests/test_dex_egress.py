from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_dex_disables_undeclared_process_proxy():
    documents = list(
        yaml.safe_load_all(
            (ROOT / "clusters/re8ch/components/auth/dex/dex-ha-deployment.yaml").read_text()
        )
    )
    deployment = next(item for item in documents if item.get("kind") == "Deployment")
    container = next(
        item
        for item in deployment["spec"]["template"]["spec"]["containers"]
        if item["name"] == "dex"
    )
    environment = {item["name"]: item.get("value") for item in container["env"]}

    assert environment["HTTP_PROXY"] == ""
    assert environment["HTTPS_PROXY"] == ""
    assert environment["NO_PROXY"] == ""


def test_dex_is_reconciled_by_the_cluster_kustomization():
    kustomization = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    assert "components/auth/dex/dex-ha-deployment.yaml" in kustomization["resources"]
