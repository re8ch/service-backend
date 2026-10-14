from pathlib import Path
import yaml

BASE = Path(__file__).parents[1] / "operations/api-conformance"


def test_runner_bootstrap_has_no_app_secrets_or_generation_policy():
    docs = list(yaml.safe_load_all((BASE / "runner-runtime.yaml").read_text()))
    assert not any(d["kind"] in {"Secret", "ClusterPolicy", "Deployment", "Job"} for d in docs)
    assert all(d["metadata"].get("namespace", "api-conformance-runners") == "api-conformance-runners" for d in docs)
    role = next(d for d in docs if d["kind"] == "Role" and d["metadata"]["name"] == "api-conformance-controller")
    assert all("secrets" not in rule["resources"] for rule in role["rules"])
    network = next(d for d in docs if d["kind"] == "CiliumNetworkPolicy")
    assert network["spec"] == {"endpointSelector": {}, "ingress": [], "egress": []}


def test_kyverno_write_permissions_stay_named():
    docs = list(yaml.safe_load_all((BASE / "runner-runtime.yaml").read_text()))
    role = next(d for d in docs if d["kind"] == "Role" and d["metadata"]["name"] == "api-conformance-pull-target")
    assert role["rules"][0]["verbs"] == ["get", "list", "create"]
    assert role["rules"][1]["resourceNames"] == ["harbor-functions-shared-pull"]
    assert role["rules"][1]["verbs"] == ["update", "patch", "delete"]
