from pathlib import Path
import yaml

BASE = Path(__file__).parents[1] / "operations/api-conformance"
AGENT_GUARDRAILS = Path(__file__).parents[1] / "clusters/re8ch/flux-federation/guardrails/agent-runtime.yaml"


def test_pull_projection_cannot_target_app_key_namespace():
    contract = yaml.safe_load((BASE / "runner-projection/policy.yaml").read_text())
    assert contract["kind"] == "ConfigMap"
    assert contract["metadata"]["namespace"] == "api-conformance-runners"
    assert contract["data"]["authority"] == "flux-system-policy-boundary"
    assert "re8ch-app-secrets" not in (BASE / "runner-projection/policy.yaml").read_text()

    policies = {
        doc["metadata"]["name"]: doc
        for doc in yaml.safe_load_all(AGENT_GUARDRAILS.read_text())
        if doc and doc.get("kind") == "ClusterPolicy"
    }
    rule = policies["api-conformance-runner-pull"]["spec"]["rules"][0]
    assert rule["match"]["any"][0]["resources"] == {
        "kinds": ["Namespace"], "names": ["api-conformance-runners"],
    }
    assert rule["generate"] == {
        "apiVersion": "v1", "kind": "Secret",
        "name": "harbor-functions-shared-pull",
        "namespace": "api-conformance-runners", "synchronize": True,
        "clone": {"namespace": "toolhive-system", "name": "harbor-functions-shared-pull"},
    }


def test_executor_cutover_keeps_only_bootstrap_reconciler():
    docs = list(yaml.safe_load_all((BASE / "runner-reconciliation.yaml").read_text()))
    assert len(docs) == 1
    assert docs[0]["metadata"]["name"] == "api-conformance-runner-bootstrap"
    assert all(d["spec"]["wait"] is True and d["spec"]["prune"] is False for d in docs)
    root = yaml.safe_load((BASE / "kustomization.yaml").read_text())
    assert "runner-reconciliation.yaml" in root["resources"]
    assert "runner-pull.yaml" not in root["resources"]
