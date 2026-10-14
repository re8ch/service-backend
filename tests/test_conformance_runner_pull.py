from pathlib import Path
import yaml

BASE = Path(__file__).parents[1] / "operations/api-conformance"


def test_pull_projection_cannot_target_app_key_namespace():
    policy = yaml.safe_load((BASE / "runner-pull.yaml").read_text())
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
