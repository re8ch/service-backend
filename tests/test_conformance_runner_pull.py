from pathlib import Path
import yaml

BASE = Path(__file__).parents[1] / "operations/api-conformance"


def test_pull_projection_cannot_target_app_key_namespace():
    policy = yaml.safe_load((BASE / "runner-projection/policy.yaml").read_text())
    rule = policy["spec"]["rules"][0]
    assert rule["match"]["any"][0]["resources"] == {
        "kinds": ["Namespace"], "names": ["api-conformance-runners"],
    }
    assert rule["generate"] == {
        "apiVersion": "v1", "kind": "Secret",
        "name": "harbor-functions-shared-pull",
        "namespace": "api-conformance-runners", "synchronize": True,
        "clone": {"namespace": "toolhive-system", "name": "harbor-functions-shared-pull"},
    }


def test_projection_waits_for_bootstrap_without_blocking_root():
    docs = list(yaml.safe_load_all((BASE / "runner-reconciliation.yaml").read_text()))
    assert docs[1]["spec"]["dependsOn"] == [{"name": docs[0]["metadata"]["name"]}]
    assert all(d["spec"]["wait"] is True and d["spec"]["prune"] is False for d in docs)
    root = yaml.safe_load((BASE / "kustomization.yaml").read_text())
    assert "runner-reconciliation.yaml" in root["resources"]
    assert "runner-pull.yaml" not in root["resources"]
