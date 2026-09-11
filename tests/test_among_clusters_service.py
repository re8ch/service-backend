from pathlib import Path
import yaml


ROOT = Path(__file__).resolve().parents[1]
IMAGE_DIGEST = "sha256:36065d8d2335e0a447d24a0b07562c859b54cee1cfb1e824e536addfe2d6557a"
CHART_DIGEST = "sha256:43c9c89801afe04bbb97b89933606a086da34d5e9bb6507d1b89ef4653fc541b"
AGENT_CHART_DIGEST = "sha256:e546d8e9056a85b813488524282727a6135a0cc544f39d2dc5a8afe23bb042a3"


def test_gitops_consumes_signed_oci_release_by_digest():
    objects = list(yaml.safe_load_all((ROOT / "platform/among-clusters/release.yaml").read_text()))
    source = next(item for item in objects if item["kind"] == "OCIRepository")
    release = next(item for item in objects if item["kind"] == "HelmRelease")
    assert source["spec"]["url"] == "oci://ghcr.io/re8ch/charts/among-clusters-hub"
    assert source["spec"]["ref"]["digest"] == CHART_DIGEST
    assert release["spec"]["values"]["image"] == {
        "repository": "ghcr.io/re8ch/among-clusters",
        "digest": IMAGE_DIGEST,
    }
    assert release["spec"]["values"]["managedAccess"]["enabled"] is True
    assert release["spec"]["values"]["migration"]["replaceCollaborationCRDs"] is False
    assert release["spec"]["values"]["replicaCount"] == 1
    assert release["spec"]["values"]["nodeSelector"] == {"kubernetes.io/hostname": "restricted-node-6dc3f140f9"}
    assert release["spec"]["values"]["onboarding"] == {
        "enabled": True,
        "publicEndpoint": "https://among-clusters.re8ch.com",
        "tenant": "byoc-pilot",
        "providerSPIFFEID": "spiffe://re8ch.internal/cluster/re8ch",
        "providerBundleDigest": "sha256:1c033ad2260b1064dd46705361dce35d1c47e8332864a88d239a90b5ffa557e8",
        "providerBundleSecret": "among-clusters-agent-identity",
        "providerQUICEndpoint": "quic://192.0.2.79:8443",
        "imageDigest": "sha256:3a6b90956f55914d595a9e24d8382b1697a8ef0ad51a65e60b77faa76ed7b692",
        "chartVersion": "0.3.25",
    }
    agent_source = next(item for item in objects if item["kind"] == "OCIRepository" and item["metadata"]["name"] == "among-clusters-agent")
    assert agent_source["spec"]["ref"]["digest"] == AGENT_CHART_DIGEST


def test_gitops_keeps_human_relay_separate_from_service_health():
    release = (ROOT / "platform/among-clusters/release.yaml").read_text()
    assert IMAGE_DIGEST in release
    assert CHART_DIGEST in release
    assert "controlled-cluster-relay" not in release
    assert "among-clusters-hub-agent" not in release
    headlamp_resources = (ROOT / "platform/headlamp/kustomization.yaml").read_text()
    assert "among-clusters-catalog.yaml" not in headlamp_resources
    assert not (ROOT / "platform/headlamp/among-clusters-catalog.yaml").exists()


def test_major_service_upgrade_waits_without_rollback():
    objects = list(yaml.safe_load_all((ROOT / "platform/among-clusters/release.yaml").read_text()))
    for release in (item for item in objects if item["kind"] == "HelmRelease"):
        assert release["spec"]["upgrade"]["timeout"] == "15m"
        assert release["spec"]["upgrade"]["remediation"] == {"retries": 0, "remediateLastFailure": False}
        assert "rollback" not in release["spec"]


def test_legacy_contracts_are_retained_for_review_but_not_reapplied():
    objects = list(yaml.safe_load_all((ROOT / "platform/among-clusters/contracts.yaml").read_text()))
    assert any(item["kind"] == "CollaborationCluster" for item in objects)
    kustomization = yaml.safe_load((ROOT / "platform/among-clusters/kustomization.yaml").read_text())
    assert "contracts.yaml" not in kustomization["resources"]


def test_legacy_agent_cleanup_is_name_scoped():
    objects = list(yaml.safe_load_all((ROOT / "platform/among-clusters/legacy-agent-cleanup.yaml").read_text()))
    role = next(item for item in objects if item["kind"] == "Role")
    job = next(item for item in objects if item["kind"] == "Job")
    assert role["rules"][0] == {
        "apiGroups": ["helm.toolkit.fluxcd.io"],
        "resources": ["helmreleases"],
        "resourceNames": ["among-clusters-hub-agent"],
        "verbs": ["get", "delete"],
    }
    assert role["rules"][1] == {
        "apiGroups": ["batch"],
        "resources": ["jobs"],
        "resourceNames": ["among-clusters-legacy-agent-cleanup"],
        "verbs": ["delete"],
    }
    assert "among-clusters-hub-agent" in job["spec"]["template"]["spec"]["containers"][0]["args"][0]


def test_byoc_pilot_enables_generation_bound_managed_access_grants():
    objects = list(yaml.safe_load_all((ROOT / "platform/among-clusters/byoc-pilot.yaml").read_text()))
    by_kind = {}
    for item in objects:
        by_kind.setdefault(item["kind"], {})[item["metadata"]["name"]] = item
    for cluster in ("qwen", "artc"):
        peer = by_kind["Peer"][f"re8ch-{cluster}"]
        assert peer["spec"]["localIdentityRef"] == "re8ch"
        policy = by_kind["PeerPolicy"][f"{cluster}-kubernetes-api"]
        assert policy["spec"]["protocols"] == ["kubernetes-api"]
        grant = by_kind["ManagedAccessGrant"][f"{cluster}-headlamp-admin"]
        assert grant["spec"]["peerRef"] == f"re8ch-{cluster}"
        assert grant["spec"]["advertisementRef"] == f"{cluster}-default-kubernetes"
        assert grant["spec"]["scope"] == "Cluster"
        assert grant["spec"]["approved"] is True


def test_byoc_pilot_is_provider_star_and_exports_mihomos_explicitly():
    objects = list(yaml.safe_load_all((ROOT / "platform/among-clusters/byoc-pilot.yaml").read_text()))
    names = {(item["kind"], item["metadata"]["name"]) for item in objects}
    assert ("Peer", "qwen-artc") not in names
    assert ("AuthenticatedLink", "qwen-artc") not in names
    assert ("PeerPolicy", "qwen-artc-default-deny") not in names

    policy = next(item for item in objects if item["kind"] == "PeerPolicy" and item["metadata"]["name"] == "re8ch-mihomos-egress")
    assert policy["spec"] == {
        "peerSelector": {"matchAllReady": True},
        "serviceClasses": ["network.egress.mihomo"],
        "protocols": ["tcp"],
        "ports": [17891],
        "directions": ["export"],
        "maxAdvertisements": 1,
    }

    pilot = list(yaml.safe_load_all((ROOT / "platform/among-clusters/mihomos-pilot.yaml").read_text()))
    service = next(item for item in pilot if item["kind"] == "Service")
    assert service["kind"] == "Service"
    assert service["metadata"]["namespace"] == "egress-fabric"
    assert service["metadata"]["labels"]["peering.re8ch.com/advertise"] == "true"
    assert service["metadata"]["annotations"]["peering.re8ch.com/target-peers"] == "*"
    assert service["spec"]["internalTrafficPolicy"] == "Local"
    assert service["spec"]["clusterIP"] == "192.0.2.78"
    assert service["spec"]["ports"][0]["port"] == 17891

    policy = next(item for item in pilot if item["kind"] == "NetworkPolicy")
    assert policy["metadata"]["name"] == "among-clusters-gateway-mihomos-egress"
    assert policy["spec"]["podSelector"]["matchLabels"]["app.kubernetes.io/name"] == "among-clusters-gateway"
    rule = policy["spec"]["egress"][0]
    assert rule["to"][0]["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"] == "egress-fabric"
    assert rule["to"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/name"] == "mihomos-node-cn"

    cilium_policy = next(item for item in pilot if item["kind"] == "CiliumNetworkPolicy")
    assert cilium_policy["spec"]["endpointSelector"]["matchLabels"] == {
        "app.kubernetes.io/name": "among-clusters-gateway"
    }
    assert cilium_policy["spec"]["egress"] == [{
        "toEntities": ["host", "remote-node"],
        "toPorts": [{"ports": [{"port": "17891", "protocol": "TCP"}]}],
    }]
    assert rule["to"][1]["ipBlock"]["cidr"] == "192.0.2.183/32"
    assert rule["to"][2]["ipBlock"]["cidr"] == "192.0.2.78/32"
    assert rule["ports"] == [{"protocol": "TCP", "port": 17891}]
    assert policy["metadata"]["annotations"]["re8ch.com/network-authority"] == "public-mesh/inventory.json#restricted-node-6dc3f140f9.publicAuthority"
    assert policy["metadata"]["annotations"]["re8ch.com/r640-dummy-purpose"] == "internal-host-endpoint-only-not-public"
