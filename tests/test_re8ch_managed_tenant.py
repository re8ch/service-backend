from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_re8ch_is_platform_managed_without_byoc():
    portfolio = yaml.safe_load(
        (ROOT / "inventory/platform/consumability/re8ch-tenant.yaml").read_text()
    )
    assert portfolio["spec"]["tenantRef"] == "re8ch"
    assert portfolio["spec"]["identityRef"] == "oidc-group:re8ch"
    assert portfolio["spec"]["hostingMode"] == "platform-managed"
    assert portfolio["spec"]["byocAllowed"] is False


def test_only_business_namespaces_are_attributed_to_re8ch():
    path = ROOT / "clusters/re8ch/components/tenancy/re8ch-managed-business-namespaces.yaml"
    objects = [item for item in yaml.safe_load_all(path.read_text()) if item]
    names = {item["metadata"]["name"] for item in objects}
    assert names == {"ledger", "neo4j", "anysite", "dataspace"}
    for item in objects:
        labels = item["metadata"]["labels"]
        assert labels["saas.re8ch.com/tenant"] == "re8ch"
        assert labels["saas.re8ch.com/layer"] == "prod"

    forbidden = {
        "among-clusters",
        "auth",
        "dex",
        "kube-system",
        "llm-gateway",
        "observability-ai",
        "qwen",
        "rook-ceph",
    }
    assert names.isdisjoint(forbidden)


def test_birfucate_publishes_re8ch_without_ai_or_byoc_grants():
    objects = list(
        yaml.safe_load_all(
            (ROOT / "platform/birfucate-metering/release.yaml").read_text()
        )
    )
    release = next(item for item in objects if item and item.get("kind") == "HelmRelease")
    tenants = release["spec"]["values"]["tenantPlatform"]["tenants"]
    re8ch = next(item for item in tenants if item["name"] == "re8ch")
    assert re8ch["visibility"] == "Private"
    assert re8ch["hostingMode"] == "platform-managed"
    assert re8ch["byocAllowed"] is False
    assert re8ch["businessNamespaces"] == ["ledger", "neo4j", "anysite", "dataspace"]
    grants = {item["consumableRef"] for item in re8ch["grants"]}
    assert "network.among-clusters.v1" in grants
    assert "ai.model-gateway.v1" not in grants
    assert "sandbox.runtime.v1" not in grants
    assert not any("byoc" in value for value in grants)
    woomou = next(
        item
        for item in re8ch["identityBindings"]
        if item["name"] == "re8ch-dex-woomou-operator"
    )
    assert woomou == {
        "name": "re8ch-dex-woomou-operator",
        "issuer": "https://dex.re8ch.com",
        "subjectHash": "sha256:8cd4777c62df7072fe9ea18fcedc0fc473f7d8a19d2fed9555c2714dac57a669",
        "role": "operator",
    }
    application_bindings = [
        item for item in re8ch["identityBindings"] if item is not woomou
    ]
    assert all(
        item["issuer"] == "https://api.re8ch.com/auth"
        for item in application_bindings
    )
