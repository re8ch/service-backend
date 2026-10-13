import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
BASE = ROOT / "operations/api-conformance"
spec = importlib.util.spec_from_file_location("api_conformance_contract", BASE / "contract.py")
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


def target(**updates):
    value = {
        "repository": "re8ch/cloud-functions",
        "baseUrl": "https://api.re8ch.com/auth/v2",
        "schema": {"url": "https://api.re8ch.com/auth/v2/openapi.json", "sha256": "a" * 64},
        "network": {"allowedHosts": ["api.re8ch.com"]},
        "operations": {"readOperationIds": ["listUsers"], "mutationOperationIds": []},
        "limits": {"maxExamples": 25, "maxRequests": 500, "ratePerSecond": 1, "timeoutSeconds": 600},
    }
    value.update(updates)
    return value


def document(method="get", operation_id="listUsers"):
    return {"openapi": "3.0.3", "paths": {"/users": {method: {"operationId": operation_id, "responses": {"200": {"description": "ok"}}}}}}


def test_target_and_operation_classification_fail_closed():
    value = contract.validate_target(target())
    assert contract.validate_document(document(), value) == {"listUsers": ("get", "/users")}
    with pytest.raises(contract.ContractError, match="document-local"):
        contract.validate_document({**document(), "components": {"schemas": {"X": {"$ref": "https://evil.invalid/x"}}}}, value)
    with pytest.raises(contract.ContractError, match="mutating"):
        contract.validate_document(document("post"), value)


def test_lifecycle_must_match_allowed_schema_operation():
    schema = document("delete", "deleteUser")
    schema["paths"]["/users/{userId}"] = schema["paths"].pop("/users")
    value = target(operations={"readOperationIds": [], "mutationOperationIds": ["deleteUser"]},
                   lifecycle={"cleanup": [{"operationId": "deleteUser", "method": "DELETE", "path": "/users/${userId}"}]})
    contract.validate_document(schema, value)
    for change in ({"path": "/organizations/${userId}"}, {"method": "POST"}, {"operationId": "unknown"}):
        step = value["lifecycle"]["cleanup"][0]
        original = step.copy()
        step.update(change)
        with pytest.raises(contract.ContractError):
            contract.validate_document(schema, value)
        step.clear()
        step.update(original)


def test_lifecycle_redirect_cannot_forward_credentials(monkeypatch):
    import sys
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    monkeypatch.setitem(sys.modules, "contract", contract)
    runner_spec = importlib.util.spec_from_file_location("conformance_runner", BASE / "runner.py")
    runner = importlib.util.module_from_spec(runner_spec)
    runner_spec.loader.exec_module(runner)
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/unexpected")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        value = target(baseUrl=f"http://127.0.0.1:{server.server_port}")
        step = {"operationId": "listUsers", "method": "GET", "path": "/users"}
        monkeypatch.setenv("AUTHORIZATION", "Bearer synthetic-test")
        with pytest.raises(contract.ContractError, match="redirects"):
            runner.lifecycle_request(value, step, {"RUN_ID": "test"})
        assert seen == ["/users"]
        for path in ("//external.invalid", "/%2e%2e/admin", "/users#fragment", "/users\\admin"):
            with pytest.raises(contract.ContractError, match="path"):
                runner.lifecycle_request(value, {**step, "path": path}, {"RUN_ID": "test"})
        assert seen == ["/users"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_mutation_requires_canary_cleanup_and_explicit_opt_in():
    value = target(operations={"readOperationIds": [], "mutationOperationIds": ["createUser"], "allowGeneratedMutations": True})
    with pytest.raises(contract.ContractError, match="canary"):
        contract.validate_target(value)
    value["canary"] = {"tenantRef": "schemathesis-canary-identity"}
    with pytest.raises(contract.ContractError, match="cleanup"):
        contract.validate_target(value)
    value["lifecycle"] = {"cleanup": [{"method": "DELETE", "path": "/users/${userId}", "expectedStatuses": [200, 404]}]}
    contract.validate_target(value)


def test_result_projection_never_contains_remote_material_or_secrets():
    result = contract.sanitized_summary(target="identity", run_id="r1", mode="read", seed=1,
        schema_sha256="a" * 64, exit_code=1,
        report={"statistic": {"total": 2, "failed": 1}, "failures": [{"response": "private user body", "Authorization": "Bearer secret"}]},
        lifecycle={"cleanup": "passed"}, audit_refs=["audit://operations/op_1", "https://not-an-audit"])
    encoded = json.dumps(result)
    assert result["status"] == "failed"
    assert result["failureFingerprints"]
    assert "private user body" not in encoded and "Bearer" not in encoded
    assert result["auditRefs"] == ["audit://operations/op_1"]
    uncertain = contract.sanitized_summary(target="payment", run_id="r2", mode="mutation", seed=2,
        schema_sha256="b" * 64, exit_code=0, report={},
        lifecycle={"cleanup": "passed", "reversal": "failed"}, audit_refs=[])
    assert uncertain["status"] == "failed"


def test_crds_and_runtime_keep_runner_unprivileged():
    crds = list(yaml.safe_load_all((BASE / "crds.yaml").read_text()))
    assert {item["metadata"]["name"] for item in crds} == {
        "apiconformancetargets.conformance.re8ch.com", "apiconformanceruns.conformance.re8ch.com"}
    runtime = list(yaml.safe_load_all((BASE / "runtime.yaml").read_text()))
    deployment = next(item for item in runtime if item.get("kind") == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    role = next(item for item in runtime if item.get("kind") == "ClusterRole")
    verbs = {(resource, verb) for rule in role["rules"] for resource in rule["resources"] for verb in rule["verbs"]}
    assert ("secrets", "get") not in verbs
    policies = [item for item in runtime if item.get("kind") == "CiliumNetworkPolicy"]
    default_deny = next(item for item in policies if item["metadata"]["name"] == "api-conformance-default-deny")
    assert default_deny["spec"] == {"endpointSelector": {}, "ingress": [], "egress": []}


def test_crd_object_schemas_do_not_mix_properties_and_additional_properties():
    """Kubernetes rejects this JSONSchema combination during CRD dry-run."""
    def visit(value):
        if isinstance(value, dict):
            assert not ("properties" in value and "additionalProperties" in value)
            assert value.get("uniqueItems") is not True
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for document in yaml.safe_load_all((BASE / "crds.yaml").read_text()):
        visit(document["spec"]["versions"][0]["schema"]["openAPIV3Schema"])


def test_runner_accepts_yaml_and_derives_hard_request_budget():
    source = (BASE / "runner.py").read_text()
    assert "yaml.safe_load(raw)" in source
    assert "request_budget // rate" in source
    controller = (BASE / "controller.py").read_text()
    assert 'status["mutationBlocked"] = True' in controller
    assert 'not status.get("mutationBlocked")' in controller
    assert 'run.get("spec", {}).get("trigger") != "manual"' in controller
    assert 'run["spec"].get("commit") == target["spec"].get("commit")' in controller


def test_root_gitops_includes_conformance_system():
    root = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    assert "../../operations/api-conformance" in root["resources"]


def test_controller_execution_permissions_are_namespace_scoped():
    manifests = list(yaml.safe_load_all((BASE / "runtime.yaml").read_text()))
    cluster_role = next(item for item in manifests if item["kind"] == "ClusterRole")
    for rule in cluster_role["rules"]:
        assert set(rule["verbs"]) <= {"get", "list", "watch"}
        assert not set(rule["resources"]) & {"pods/log", "jobs", "secrets"}
    role = next(item for item in manifests if item["kind"] == "Role")
    binding = next(item for item in manifests if item["kind"] == "RoleBinding")
    assert role["metadata"]["namespace"] == binding["metadata"]["namespace"] == "api-conformance-system"
    assert binding["roleRef"]["kind"] == "Role"
    assert binding["roleRef"]["name"] == role["metadata"]["name"]


def test_rendered_code_version_is_pinned_for_controller_and_runner():
    import shutil
    import subprocess

    if not shutil.which("kustomize"):
        pytest.skip("kustomize required for rendered name-reference check")
    rendered = subprocess.check_output(["kustomize", "build", str(BASE)], text=True)
    manifests = list(yaml.safe_load_all(rendered))
    configmap = next(item for item in manifests if item["kind"] == "ConfigMap")
    name = configmap["metadata"]["name"]
    assert name.startswith("api-conformance-code-")
    deployment = next(item for item in manifests if item["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    assert next(v for v in pod["volumes"] if v["name"] == "code")["configMap"]["name"] == name
    assert next(e for e in pod["containers"][0]["env"] if e["name"] == "CODE_CONFIG_MAP")["value"] == name
    assert '"configMap": {"name": CODE_CONFIG_MAP}' in configmap["data"]["controller.py"]


def test_pilot_targets_are_digest_pinned_and_fail_closed():
    targets = list(yaml.safe_load_all((BASE / "targets.yaml").read_text()))
    assert {item["metadata"]["name"] for item in targets} == {
        "cloud-functions-identity", "cloud-functions-payment-credits", "cloud-functions-commerce"}
    for item in targets:
        assert item["spec"]["suspend"] is True
        assert len(item["spec"]["schema"]["sha256"]) == 64
        assert item["spec"]["commit"] == "e37a09c132aaca7779495f78d10deef4547cab5f"
    mutation_targets = [item for item in targets if item["spec"]["operations"]["mutationOperationIds"]]
    assert mutation_targets and all(item["spec"]["operations"]["allowGeneratedMutations"] is False for item in mutation_targets)
