import importlib.util
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = ROOT / "operations/releases/static-domain-authority.yaml"
DOMAINS = {
    "agent-runtime", "identity-access", "observability-metering", "networking",
    "data-storage", "commerce-billing", "delivery-products", "operations-ui", "tenant-runtime",
}
MAIN = {"data-storage", "commerce-billing", "delivery-products", "operations-ui", "tenant-runtime"}

spec = importlib.util.spec_from_file_location(
    "main_domain_profile", ROOT / "operations/releases/main_domain_profile.py")
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


def authority():
    return yaml.safe_load(AUTHORITY.read_text())["spec"]


def test_authority_declares_nine_isolated_domains_and_serial_rollout():
    value = authority()
    assert set(value["domains"]) == DOMAINS
    assert set(value["rolloutPolicy"]["order"]) == DOMAINS
    assert value["rolloutPolicy"]["productionCutovers"] == "serial"
    environments = {item["execution"]["protectedEnvironment"] for item in value["domains"].values()}
    audiences = {item["execution"]["audience"] for item in value["domains"].values()}
    groups = {item["execution"]["kubernetesGroup"] for item in value["domains"].values()}
    assert len(environments) == len(audiences) == len(groups) == 9
    assert value["protectedEnvironmentPolicy"] == {
        "requiredReviewers": ["Woomou"],
        "preventSelfReview": False,
        "administratorsMayBypass": False,
        "deploymentBranches": ["main"],
        "requiredSecrets": ["KUBE_API_CA_PEM"],
        "notes": [
            "Woomou is the only current member of both k8s-admins and gitops-breakglass.",
            "Preventing self review would deadlock every protected deployment until another reviewer is enrolled.",
        ],
    }


def test_five_main_domains_have_separate_bootstrap_environments_and_cannot_shadow_without_repo_mapping():
    domains = authority()["domains"]
    for name in MAIN:
        item = domains[name]
        assert item["bootstrap"] == {
            "repository": "re8ch/service-backend",
            "workflow": ".github/workflows/main-domain-release.yml",
        }
        assert item["execution"]["protectedEnvironment"] == f"release-executor-{name}"
        assert item["source"]["mappingState"] == "pending-enterprise-inventory"
        assert item["source"]["repository"] is None
        assert item["state"] == "inventory"


def test_profile_validator_rejects_cross_domain_environment(tmp_path):
    original = yaml.safe_load((ROOT / "contracts/releases/main-domain/main-delivery-products-aesthete.yaml").read_text())
    profile.CONTRACT_ROOT = tmp_path
    path = tmp_path / "profile-validator-fixture.yaml"
    original["spec"]["provenance"]["environment"] = "release-executor-data-storage"
    path.write_text(yaml.safe_dump(original))
    with pytest.raises(ValueError, match="provenance environment"):
        profile.validate_contract(path)
