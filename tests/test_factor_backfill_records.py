import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
MIGRATIONS = ROOT / "operations/github-transaction-context/migrations"


def records():
    return [json.loads(path.read_text()) for path in sorted(MIGRATIONS.glob("*.json"))]


def test_factor_backfill_records_are_non_destructive():
    assert records()
    for record in records():
        if record["kind"] == "PullRequestFactorBackfill":
            assert record["safety"]["closedPullRequests"] == 0
            assert record["safety"]["preservedOriginalBodies"] is True
            pull_requests = [item["pullRequest"] for item in record["transactions"]]
            assert len(pull_requests) == len(set(pull_requests))
            for transaction in record["transactions"]:
                assert transaction["primaryFactor"]
                assert transaction["result"] == "complete"
                assert transaction["project"]
        elif record["kind"] == "PullRequestFactorBackfillRecord":
            assert record["batch"]["pullRequestCount"] == len(record["pullRequests"])
            assert record["safety"]["pullRequestsClosed"] == 0
            assert record["safety"]["pullRequestsMerged"] == 0
            assert record["safety"]["existingBodyPreserved"] is True
            assert record["safety"]["credentialMaterialRecorded"] is False
        elif record["kind"] == "PullRequestFactorBulkBackfillRecord":
            result = record["result"]
            assert result["attempted"] == result["succeeded"] + result["failed"] + result["skipped"]
            assert record["scope"]["factorGroups"] == record["safety"]["issueFactorsCreatedOrReused"]
            assert record["safety"]["pullRequestsClosed"] == 0
            assert record["safety"]["pullRequestsMerged"] == 0
            assert record["safety"]["existingBodiesPreserved"] is True
            assert sum(len(exception["pullRequests"]) for exception in record["exceptions"]) == result["failed"]
        elif record["kind"] == "EnterprisePullRequestContextBackfillRecord":
            result = record["result"]
            scope = record["scope"]
            assert result["attempted"] == result["succeeded"] + result["failed"] + result["skipped"]
            assert scope["detachedSnapshotRows"] == scope["trueDetachedPullRequests"] + scope["boundedExceptions"]
            assert len(record["boundedExceptions"]) == scope["boundedExceptions"]
            assert record["projectReadback"]["factorItemsComplete"] == scope["factorGroups"]
            assert record["safety"]["pullRequestsClosed"] == 0
            assert record["safety"]["pullRequestsMerged"] == 0
            assert record["safety"]["pullRequestBasesChanged"] == 0
        else:
            raise AssertionError(f"unsupported migration record kind: {record['kind']}")


def test_identity_human_wave_records_distinct_primary_factors():
    record = json.loads(
        (MIGRATIONS / "2026-11-26-identity-human-wave.json").read_text()
    )
    factors = [item["primaryFactor"] for item in record["transactions"]]
    assert factors == [
        "re8ch/identity-gitops#106",
        "re8ch/identity-gitops#107",
    ]


def test_automation_record_has_native_primary_factor_readback():
    record = json.loads(
        (MIGRATIONS / "advanced-fabrics-core-github-actions-20261126.json").read_text()
    )
    factor = record["primaryFactor"]
    assert factor["issueType"] in {"Task", "Bug", "Feature"}
    assert factor["parent"]
    assert factor["project"]
    for pull_request in record["pullRequests"]:
        assert pull_request["stateBefore"] == "OPEN"
        assert pull_request["stateAfter"] == "OPEN"
        assert pull_request["nativeClosingReferenceReadback"] is True
        assert pull_request["bodySha256Before"] != pull_request["bodySha256After"]


def test_automation_aggregation_key_matches_bounded_batch():
    record = json.loads(
        (MIGRATIONS / "advanced-fabrics-core-github-actions-20261126.json").read_text()
    )
    batch = record["batch"]
    assert batch["aggregationKey"] == (
        f"dependency-update:{batch['repository']}:{batch['ecosystem']}"
    )
