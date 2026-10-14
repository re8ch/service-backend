"""Guard the anonymous pilot against accidentally enabling production writes."""
from pathlib import Path
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


def test_authenticated_targets_remain_suspended():
    targets = list(yaml.safe_load_all((ROOT / "targets.yaml").read_text()))
    assert len(targets) == 3
    assert all(target["spec"]["suspend"] is True for target in targets)


def test_controller_dns_policy_supports_fqdn_learning():
    docs = list(yaml.safe_load_all((ROOT / "runtime.yaml").read_text()))
    policy = next(d for d in docs if d["kind"] == "CiliumNetworkPolicy"
                  and d["metadata"]["name"] == "api-conformance-controller")
    dns = policy["spec"]["egress"][1]["toPorts"][0]
    assert dns["rules"] == {"dns": [{"matchPattern": "*"}]}
    assert {p["protocol"] for p in dns["ports"]} == {"TCP", "UDP"}
