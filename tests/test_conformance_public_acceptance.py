"""Guard the anonymous pilot against accidentally enabling production writes."""
from pathlib import Path
import re
import yaml

ROOT = Path(__file__).parents[1] / "operations/api-conformance"


def test_public_acceptance_is_bounded_and_has_no_credentials_or_mutations():
    target = yaml.safe_load((ROOT / "public-acceptance.yaml").read_text())["spec"]
    assert target["repository"] == "re8ch/cloud-functions"
    assert "authSecretRef" not in target
    assert "lifecycle" not in target
    assert target["operations"] == {
        "readOperationIds": ["commerceHealth", "listCommerceOrders"],
        "mutationOperationIds": [], "allowGeneratedMutations": False,
    }
    assert target["limits"] == {
        "maxExamples": 5, "maxRequests": 100, "ratePerSecond": 1,
        "timeoutSeconds": 180,
    }
    assert target["network"]["allowedHosts"] == ["api.re8ch.com"]
    assert target["network"]["pinnedEgressIPs"] == {"api.re8ch.com": "192.0.2.138"}


def test_authenticated_targets_remain_suspended():
    targets = list(yaml.safe_load_all((ROOT / "targets.yaml").read_text()))
    assert len(targets) == 3
    assert all(target["spec"]["suspend"] is True for target in targets)


def test_enabled_public_target_requires_isolated_digest_pinned_runner():
    target = yaml.safe_load((ROOT / "public-acceptance.yaml").read_text())["spec"]
    if target["suspend"]:
        return
    docs = list(yaml.safe_load_all((ROOT / "runtime.yaml").read_text()))
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    env = {item["name"]: item.get("value") for item in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["RUNNER_NAMESPACE"] == "api-conformance-runners"
    assert re.fullmatch(r"registry\.re8ch\.com/functions-shared/api-conformance-runner@sha256:[a-f0-9]{64}", env.get("RUNNER_IMAGE", ""))


def test_controller_dns_policy_supports_fqdn_learning():
    docs = list(yaml.safe_load_all((ROOT / "runtime.yaml").read_text()))
    policy = next(d for d in docs if d["kind"] == "CiliumNetworkPolicy"
                  and d["metadata"]["name"] == "api-conformance-controller")
    dns = policy["spec"]["egress"][1]["toPorts"][0]
    assert dns["rules"] == {"dns": [{"matchPattern": "*"}]}
    assert {p["protocol"] for p in dns["ports"]} == {"TCP", "UDP"}


def test_controller_github_egress_uses_local_proxy_and_excludes_kubernetes_api():
    docs = list(yaml.safe_load_all((ROOT / "runtime.yaml").read_text()))
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    env = {item["name"]: item.get("value") for item in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["HTTPS_PROXY"] == "http://identity-egress-control-plane.egress-fabric.svc.cluster.local:17893"
    assert "192.0.2.183/8" in env["NO_PROXY"]
    policy = next(d for d in docs if d["kind"] == "CiliumNetworkPolicy"
                  and d["metadata"]["name"] == "api-conformance-controller")
    assert {"toEntities": ["host"], "toPorts": [{"ports": [{"port": "17893", "protocol": "TCP"}]}]} in policy["spec"]["egress"]


def test_all_dns_rules_select_actual_cluster_resolver():
    for filename in ("runtime.yaml", "result-runtime.yaml"):
        docs = list(yaml.safe_load_all((ROOT / filename).read_text()))
        for policy in (d for d in docs if d["kind"] == "CiliumNetworkPolicy"):
            for rule in policy["spec"].get("egress", []):
                if any(p.get("rules", {}).get("dns") for p in rule.get("toPorts", [])):
                    assert rule["toEndpoints"] == [{"matchLabels": {
                        "k8s:io.kubernetes.pod.namespace": "kube-system",
                        "k8s:app.kubernetes.io/name": "advanced-fabric-doh",
                    }}]
    source = (ROOT / "controller.py").read_text()
    assert '"k8s:app.kubernetes.io/name": "advanced-fabric-doh"' in source
    assert '"k8s:k8s-app": "kube-dns"' not in source
