import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("tenant_claims", ROOT / "clusters/re8ch/components/ai-operations/tenant_claims.py")
claims = importlib.util.module_from_spec(spec)
spec.loader.exec_module(claims)


def claim(kind="Deployment", exposure=False):
    return {
        "apiVersion": "tenancy.re8ch.com/v1alpha1", "kind": "TenantResourceClaim",
        "metadata": {"name": "web", "uid": "1234"},
        "spec": {"tenantRef": "acme", "capability": "compute.workload.v1", "parameters": {
            "kind": kind, "imageDigest": "registry.re8ch.com/acme/web@sha256:" + "a" * 64,
            "resources": {"cpu": "500m", "memory": "1Gi", "ephemeralStorage": "2Gi"},
            "command": [], "args": [], "env": {}, "volumeMounts": [], "egressClass": "metered-public",
            "exposure": {"enabled": exposure, "port": 8080, "healthPath": "/healthz"} if exposure else {"enabled": False},
            **({"replicas": 2} if kind == "Deployment" else {"parallelism": 1, "completions": 1}),
        }},
    }


def test_deployment_is_hardened_and_has_private_network_denies():
    rendered = claims.render_resources(claim(), "acme-runtime")
    deployment = rendered[0]
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert deployment["kind"] == "Deployment"
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert deployment["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
    assert deployment["spec"]["template"]["spec"]["serviceAccountName"] == "tenant-workload"
    policy = rendered[1]
    assert "10.0.0.0/8" in policy["spec"]["egress"][1]["to"][0]["ipBlock"]["except"]


def test_public_exposure_has_deterministic_anonymous_route():
    rendered = claims.render_resources(claim(exposure=True), "acme-runtime")
    assert [item["kind"] for item in rendered] == ["Deployment", "NetworkPolicy", "Service", "HTTPRoute"]
    route = rendered[-1]
    assert route["spec"]["hostnames"] == ["acme-web.apps.re8ch.com"]
    assert route["spec"]["parentRefs"][0]["sectionName"] == "apps-wildcard-https"
    assert route["spec"]["parentRefs"][0]["namespace"] == "kube-system"
    assert claims.binding_status(claim(exposure=True), "acme-runtime")["endpoint"]["anonymous"] is True


def test_volume_has_no_owner_reference_and_uses_retain():
    volume = {"metadata": {"name": "data", "uid": "5678"}, "spec": {"tenantRef": "acme", "capability": "storage.volume.v1", "parameters": {"sizeGiB": 20, "accessMode": "ReadWriteOnce", "storageClass": "rbd-r640", "retentionPolicy": "Retain"}}}
    pvc = claims.render_resources(volume, "acme-runtime")[0]
    assert pvc["kind"] == "PersistentVolumeClaim"
    assert "ownerReferences" not in pvc["metadata"]
    assert pvc["metadata"]["annotations"]["tenancy.re8ch.com/retention-policy"] == "Retain"


def test_long_claim_names_keep_collision_resistant_resource_names():
    left = claims.resource_name("a" * 62 + "b")
    right = claims.resource_name("a" * 62 + "c")
    assert len(left) <= 63
    assert left != right
