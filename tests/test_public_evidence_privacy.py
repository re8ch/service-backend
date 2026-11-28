import importlib.util
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "public_evidence_validate", ROOT / "operations/public-evidence/validate.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def envelope():
    return {
        "version": 1,
        "claimType": "health",
        "subjectHandle": "hmac-sha256:" + "a" * 64,
        "sourceRevision": "b" * 40,
        "policyDigest": "sha256:" + "c" * 64,
        "timeBucket": "2026-11-01",
        "publishAfter": "2026-11-02",
        "result": "healthy",
        "cohortBand": "10-49",
        "metricBand": "high",
        "commitmentRoot": "sha256:" + "d" * 64,
        "evidenceClass": "public-aggregate",
    }


def test_valid_public_envelope_is_accepted():
    MODULE.validate_document(envelope())


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("tenant", "example"),
        ("detail", "person@example.com"),
        ("detail", "10.0.0.1"),
        ("detail", "123e4567-e89b-12d3-a456-426614174000"),
        ("detail", "2026-11-01T12:34:56Z"),
    ],
)
def test_private_witness_fields_and_values_are_rejected(key, value):
    candidate = envelope()
    candidate[key] = value
    with pytest.raises(MODULE.EvidenceError):
        MODULE.validate_document(candidate)


def test_low_entropy_subject_and_short_delay_are_rejected():
    candidate = envelope()
    candidate["subjectHandle"] = "sha256:" + "a" * 64
    with pytest.raises(MODULE.EvidenceError, match="schema-invalid"):
        MODULE.validate_document(candidate)
    candidate = envelope()
    candidate["publishAfter"] = candidate["timeBucket"]
    with pytest.raises(MODULE.EvidenceError, match="publication-delay-too-short"):
        MODULE.validate_document(candidate)


def test_suppressed_cohort_cannot_publish_a_metric_band():
    candidate = envelope()
    candidate["cohortBand"] = "suppressed"
    with pytest.raises(MODULE.EvidenceError, match="suppressed-cohort-leaks-metric"):
        MODULE.validate_document(candidate)


def test_policy_covers_every_public_surface_and_private_witness_class():
    policy = yaml.safe_load(
        (ROOT / "contracts/public-evidence-privacy/policy.yaml").read_text()
    )["spec"]
    assert {"repository", "issue", "discussion", "wiki", "pages", "actions-summary", "actions-artifact"} <= set(
        policy["publicSurfaces"]
    )
    assert policy["aggregation"]["minimumCohortSize"] == 5
    assert policy["commitment"]["minimumNonceBytes"] >= 32
    assert policy["privateWitness"]["rawEvidenceStore"] == "approved-restricted-system"
