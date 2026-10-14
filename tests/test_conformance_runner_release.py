from pathlib import Path
import yaml

ROOT=Path(__file__).parents[1]


def test_runner_pull_uses_exact_managed_secret():
    docs=list(yaml.safe_load_all((ROOT/"operations/api-conformance/runtime.yaml").read_text()))
    policy=next(x for x in docs if x["kind"]=="ClusterPolicy")
    rule=policy["spec"]["rules"][0]
    assert rule["match"]["any"][0]["resources"]["names"]==["api-conformance-system"]
    assert rule["generate"]["clone"]=={"namespace":"toolhive-system","name":"harbor-functions-shared-pull"}
    source=next(x for x in docs if x["kind"]=="Role" and x["metadata"]["name"]=="api-conformance-pull-source")
    assert source["rules"][0]["verbs"]==["get"]
    assert source["rules"][0]["resourceNames"]==["harbor-functions-shared-pull"]
    target=next(x for x in docs if x["kind"]=="Role" and x["metadata"]["name"]=="api-conformance-pull-target")
    assert target["rules"][1]["resourceNames"]==["harbor-functions-shared-pull"]
    assert "list" not in target["rules"][1]["verbs"]


def test_release_requires_version_scan_and_signature():
    text=(ROOT/".github/workflows/conformance-runner-release.yml").read_text()
    for required in ["merge-base --is-ancestor", "4.29.1", "HIGH,CRITICAL --exit-code 1", "cosign verify", "functions-shared/api-conformance-runner", "provenance: mode=max", "sbom: true"]:
        assert required in text
