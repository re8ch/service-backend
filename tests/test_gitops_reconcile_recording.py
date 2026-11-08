import json
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
POLICY = ROOT / "operations/github-maintenance/gitops-reconcile-recording.json"


def test_recording_policy_uses_wiki_for_success_and_issue_for_incidents():
    policy = json.loads(POLICY.read_text())
    assert policy["kind"] == "GitOpsReconcileRecordingPolicy"
    assert policy["healthy"]["destination"] == "wiki-monthly-journal"
    assert policy["healthy"]["repositoryScope"] == "source"
    assert policy["incident"]["contentType"] == "Issue"
    assert set(policy["incident"]["states"]) == {"Failed", "Degraded", "Unknown"}


def test_recording_policy_has_unique_sources():
    policy = json.loads(POLICY.read_text())
    repositories = [source["repository"] for source in policy["sources"]]
    assert len(repositories) == len(set(repositories)) == 6


def test_observation_workflow_matrix_matches_policy():
    policy = json.loads(POLICY.read_text())
    workflow = yaml.safe_load((ROOT / ".github/workflows/gitops-observation.yml").read_text())
    matrix = workflow["jobs"]["record"]["strategy"]["matrix"]["include"]
    actual = {(row["owner"] + "/" + row["repo"], row["workflow"]) for row in matrix}
    expected = {(row["repository"], row["workflow"]) for row in policy["sources"]}
    assert actual == expected
