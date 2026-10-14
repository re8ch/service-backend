from pathlib import Path
import importlib.util
import pytest
import yaml

BASE = Path(__file__).parents[1] / "operations/api-conformance"


def test_runner_bootstrap_has_no_app_secrets_or_generation_policy():
    docs = list(yaml.safe_load_all((BASE / "runner-bootstrap/resources.yaml").read_text()))
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


def test_kyverno_permissions_are_confined_to_guarded_namespace():
    docs = list(yaml.safe_load_all((BASE / "runner-bootstrap/resources.yaml").read_text()))
    role = next(d for d in docs if d["kind"] == "Role" and d["metadata"]["name"] == "api-conformance-pull-target")
    assert role["metadata"]["namespace"] == "api-conformance-runners"
    assert role["rules"] == [{"apiGroups": [""], "resources": ["secrets"],
                              "verbs": ["get", "list", "create", "update", "patch", "delete"]}]


def test_isolated_job_has_no_static_credentials_or_cross_namespace_mount(monkeypatch):
    monkeypatch.syspath_prepend(str(BASE))
    spec = importlib.util.spec_from_file_location("isolated_controller", BASE / "controller.py")
    c = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(c)
    monkeypatch.setattr(c, "RUNNER_NAMESPACE", "api-conformance-runners")
    monkeypatch.setattr(c, "validate_target", lambda value: value)
    monkeypatch.setattr(c, "patch_status", lambda *a: None)
    calls = []
    def request(method, path, body, content_type="application/json"):
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
    assert job["spec"]["suspend"] is True
    assert [v["name"] for v in pod["volumes"]] == ["tmp"]
    assert calls[1][1]["metadata"]["namespace"] == "api-conformance-runners"
    assert calls[2] == ("/apis/batch/v1/namespaces/api-conformance-runners/jobs/test-run", {"spec": {"suspend": False}})
    calls.clear()
    target["spec"]["authSecretRef"] = {"name": "forbidden"}
    with pytest.raises(c.ContractError, match="credential broker"):
        c.create_run(target, "read", "manual", "test-run")
    assert calls == []


def test_pinned_public_ip_is_confined_to_the_run_policy(monkeypatch):
    monkeypatch.syspath_prepend(str(BASE))
    spec = importlib.util.spec_from_file_location("isolated_controller_pinned", BASE / "controller.py")
    c = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(c)
    monkeypatch.setattr(c, "RUNNER_NAMESPACE", "api-conformance-runners")
    monkeypatch.setattr(c, "patch_status", lambda *a: None)
    calls = []
    def request(method, path, body, content_type="application/json"):
        calls.append((path, body))
        return {"metadata": {"uid": "test-job"}}
    monkeypatch.setattr(c, "kube_request", request)
    target = {"metadata": {"name": "target"}, "spec": {
        "repository": "re8ch/cloud-functions", "baseUrl": "https://api.re8ch.com/commerce",
        "schema": {"url": "https://api.re8ch.com/commerce/v1/openapi.yaml", "sha256": "0" * 64},
        "network": {"allowedHosts": ["api.re8ch.com"], "pinnedEgressIPs": {"api.re8ch.com": "192.0.2.138"}},
        "limits": {"timeoutSeconds": 180},
    }}
    c.create_run(target, "read", "manual", "test-run")
    pod = calls[0][1]["spec"]["template"]["spec"]
    assert pod["hostAliases"] == [{"ip": "192.0.2.138", "hostnames": ["api.re8ch.com"]}]
    policy = calls[1][1]
    assert policy["spec"]["endpointSelector"]["matchLabels"] == {"conformance.re8ch.com/run": "test-run"}
    assert policy["spec"]["egress"] == [{
        "toCIDR": ["192.0.2.138/32"],
        "toPorts": [{"ports": [{"port": "443", "protocol": "TCP"}]}],
    }]
    target["spec"]["network"]["pinnedEgressIPs"] = {"api.re8ch.com": "192.0.2.213"}
    with pytest.raises(c.ContractError, match="public IPv4"):
        c.create_run(target, "read", "manual", "test-run")
    assert len(calls) == 3
