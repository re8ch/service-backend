from pathlib import Path
import importlib.util
import pytest
import yaml

BASE = Path(__file__).parents[1] / "operations/api-conformance"


def test_runner_bootstrap_has_no_app_secrets_or_generation_policy():
    docs = list(yaml.safe_load_all((BASE / "runner-runtime.yaml").read_text()))
    assert not any(d["kind"] in {"Secret", "Deployment", "Job"} for d in docs)
    assert not any("generate" in rule for d in docs if d["kind"] == "ClusterPolicy" for rule in d["spec"]["rules"])
    assert all(d["metadata"].get("namespace", "api-conformance-runners") == "api-conformance-runners" for d in docs)
    role = next(d for d in docs if d["kind"] == "Role" and d["metadata"]["name"] == "api-conformance-controller")
    assert all("secrets" not in rule["resources"] for rule in role["rules"])
    network = next(d for d in docs if d["kind"] == "CiliumNetworkPolicy")
    assert network["spec"] == {"endpointSelector": {}, "ingress": [], "egress": []}
    policy = next(d for d in docs if d["kind"] == "ClusterPolicy")
    assert policy["spec"]["validationFailureAction"] == "Enforce"
    rule = policy["spec"]["rules"][0]
    assert rule["match"]["any"][0]["resources"]["namespaces"] == ["api-conformance-runners"]
    assert rule["validate"]["pattern"] == {
        "metadata": {"name": "harbor-functions-shared-pull"},
        "type": "kubernetes.io/dockerconfigjson",
    }


def test_kyverno_write_permissions_stay_named():
    docs = list(yaml.safe_load_all((BASE / "runner-runtime.yaml").read_text()))
    role = next(d for d in docs if d["kind"] == "Role" and d["metadata"]["name"] == "api-conformance-pull-target")
    assert role["rules"][0]["verbs"] == ["get", "list", "create"]
    assert role["rules"][1]["resourceNames"] == ["harbor-functions-shared-pull"]
    assert role["rules"][1]["verbs"] == ["update", "patch", "delete"]


def test_isolated_job_has_no_static_credentials_or_cross_namespace_mount(monkeypatch):
    monkeypatch.syspath_prepend(str(BASE))
    spec = importlib.util.spec_from_file_location("isolated_controller", BASE / "controller.py")
    c = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(c)
    monkeypatch.setattr(c, "RUNNER_NAMESPACE", "api-conformance-runners")
    monkeypatch.setattr(c, "validate_target", lambda value: value)
    monkeypatch.setattr(c, "patch_status", lambda *a: None)
    calls = []
    def request(method, path, body):
        calls.append((path, body))
        return {"metadata": {"uid": "test-job"}}
    monkeypatch.setattr(c, "kube_request", request)
    target = {"metadata": {"name": "target"}, "spec": {}}
    c.create_run(target, "read", "manual", "test-run")
    path, job = calls[0]
    assert path == "/apis/batch/v1/namespaces/api-conformance-runners/jobs"
    pod = job["spec"]["template"]["spec"]
    assert job["metadata"]["namespace"] == "api-conformance-runners"
    assert pod["automountServiceAccountToken"] is False
    assert [v["name"] for v in pod["volumes"]] == ["tmp"]
    assert calls[1][1]["metadata"]["namespace"] == "api-conformance-runners"
    calls.clear()
    target["spec"]["authSecretRef"] = {"name": "forbidden"}
    with pytest.raises(c.ContractError, match="credential broker"):
        c.create_run(target, "read", "manual", "test-run")
    assert calls == []
