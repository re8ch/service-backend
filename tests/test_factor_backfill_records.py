import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
MIGRATIONS = ROOT / "operations/github-transaction-context/migrations"


def test_factor_backfill_records_preserve_primary_ownership():
    records = [json.loads(path.read_text()) for path in MIGRATIONS.glob("*.json")]
    assert records
    for record in records:
        assert record["kind"] == "PullRequestFactorBackfill"
        assert record["safety"]["closedPullRequests"] == 0
        assert record["safety"]["preservedOriginalBodies"] is True
        pull_requests = [item["pullRequest"] for item in record["transactions"]]
        assert len(pull_requests) == len(set(pull_requests))
        for transaction in record["transactions"]:
            assert transaction["primaryFactor"]
            assert transaction["result"] == "complete"
            assert transaction["project"]


def test_identity_human_wave_records_distinct_primary_factors():
    record = json.loads(
        (MIGRATIONS / "2026-11-26-identity-human-wave.json").read_text()
    )
    factors = [item["primaryFactor"] for item in record["transactions"]]
    assert factors == [
        "re8ch/identity-gitops#106",
        "re8ch/identity-gitops#107",
    ]
