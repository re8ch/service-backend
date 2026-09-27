from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_dex_egress_matches_each_issuer_runtime():
    for manifest in ("dex-ha-deployment.yaml", "dex-service-deployment.yaml"):
        documents = list(yaml.safe_load_all(
            (ROOT / "clusters/re8ch/components/auth/dex" / manifest).read_text()))
        deployment = next(item for item in documents if item.get("kind") == "Deployment")
        container = next(
            item for item in deployment["spec"]["template"]["spec"]["containers"]
            if item["name"] == "dex")
        environment = {item["name"]: item.get("value") for item in container["env"]}

        assert ".svc" in environment["NO_PROXY"]
        assert "10.0.0.0/8" in environment["NO_PROXY"]
        pod = deployment["spec"]["template"]["spec"]
        if deployment["metadata"]["name"] == "dex-service":
            expected = "http://10.250.0.104:17893"
            assert environment["HTTP_PROXY"] == expected
            assert environment["HTTPS_PROXY"] == expected
            assert pod["hostNetwork"] is True
            assert pod["dnsPolicy"] == "Default"
        else:
            expected = "http://identity-egress-control-plane.egress-fabric.svc.cluster.local:17893"
            assert environment["HTTP_PROXY"] == expected
            assert environment["HTTPS_PROXY"] == expected
            assert pod["dnsPolicy"] == "ClusterFirst"


def test_dex_workloads_are_retired_from_the_cluster_kustomization():
    kustomization = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    assert not any("components/auth/dex/" in resource for resource in kustomization["resources"])
    assert "components/auth/migrate-dex-github-credentials.yaml" not in kustomization["resources"]
