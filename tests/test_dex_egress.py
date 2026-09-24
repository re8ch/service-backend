from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_dex_uses_the_control_plane_local_egress_service():
    for manifest in ("dex-ha-deployment.yaml", "dex-service-deployment.yaml"):
        documents = list(yaml.safe_load_all(
            (ROOT / "clusters/re8ch/components/auth/dex" / manifest).read_text()))
        deployment = next(item for item in documents if item.get("kind") == "Deployment")
        container = next(
            item for item in deployment["spec"]["template"]["spec"]["containers"]
            if item["name"] == "dex")
        environment = {item["name"]: item.get("value") for item in container["env"]}

        expected = "http://identity-egress-control-plane.egress-fabric.svc.cluster.local:17893"
        assert environment["HTTP_PROXY"] == expected
        assert environment["HTTPS_PROXY"] == expected
        assert ".svc" in environment["NO_PROXY"]
        assert "192.0.2.183/8" in environment["NO_PROXY"]
        assert deployment["spec"]["template"]["spec"]["dnsPolicy"] == "ClusterFirst"


def test_dex_is_reconciled_by_the_cluster_kustomization():
    kustomization = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    assert "components/auth/dex/dex-ha-deployment.yaml" in kustomization["resources"]
