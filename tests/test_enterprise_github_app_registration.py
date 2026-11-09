from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
REGISTRATION = (
    ROOT
    / "operations/migrations/github-first-control-plane/github-app-registrations"
    / "re8ch-enterprise-governance.yaml"
)
SUITE = REGISTRATION.with_name("re8ch-enterprise-app-suite.yaml")
MATRIX = REGISTRATION.with_name("enterprise-organization-installation-matrix.yaml")


def registration():
    return yaml.safe_load(REGISTRATION.read_text())


def test_enterprise_governance_app_has_future_ready_governance_surface():
    spec = registration()["spec"]
    assert spec["owner"] == {"type": "enterprise", "slug": "re8ch"}
    assert spec["installationPolicy"]["allRepositories"] is False
    assert spec["installationPolicy"]["repositories"] == "selected"

    assert {
        "enterpriseOrganizationInstallations",
        "enterpriseRulesets",
    } <= set(spec["permissions"]["enterprise"])
    assert {
        "administration",
        "customProperties",
        "organizationHooks",
        "organizationProjects",
        "variables",
    } <= set(spec["permissions"]["organization"])
    assert {
        "actions",
        "administration",
        "checks",
        "deployments",
        "environments",
        "issues",
        "pullRequests",
        "repositoryCustomProperties",
        "repositoryHooks",
        "variables",
    } <= set(spec["permissions"]["repository"])


def test_enterprise_governance_app_does_not_absorb_executor_or_secret_authority():
    spec = registration()["spec"]
    repository_permissions = spec["permissions"]["repository"]
    assert repository_permissions["contents"] == "read"
    assert repository_permissions["workflows"] == "read"
    assert repository_permissions["secrets"] == "read"
    assert "repository-content-write" in spec["capabilities"]["denied"]
    assert "workflow-file-write" in spec["capabilities"]["denied"]
    assert "secret-value-return" in spec["capabilities"]["denied"]
    assert "ruleset-bypass" in spec["capabilities"]["denied"]


def test_governance_changes_are_reviewed_and_verified():
    contract = registration()["spec"]["changeContract"]
    assert contract["phases"] == ["plan", "apply", "status"]
    assert "reviewed-declaration" in contract["applyRequires"]
    assert "read-back-effective-state" in contract["verification"]
    assert "audit-receipt" in contract["verification"]


def test_enterprise_suite_uses_durable_responsibility_boundaries():
    spec = yaml.safe_load(SUITE.read_text())["spec"]
    apps = spec["apps"]
    assert len(apps) == 7
    assert {app["name"] for app in apps} == {
        "re8ch-enterprise-governance",
        "re8ch-enterprise-delivery",
        "re8ch-enterprise-security",
        "re8ch-enterprise-collaboration",
        "re8ch-enterprise-developer-ai",
        "re8ch-enterprise-identity",
        "re8ch-enterprise-billing",
    }
    responsibilities = {
        responsibility
        for app in apps
        for responsibility in app["responsibilities"]
    }
    assert {
        "enterprise-rulesets",
        "contents-and-workflows",
        "advanced-security-products",
        "issues-and-pull-requests",
        "agent-tasks-secrets-and-variables",
        "enterprise-scim",
        "enterprise-billing",
    } <= responsibilities
    assert "ruleset-bypass" in spec["exclusions"]


def test_installation_matrix_covers_every_observed_enterprise_organization():
    spec = yaml.safe_load(MATRIX.read_text())["spec"]
    assert len(spec["organizations"]) == 25
    assert len(spec["apps"]) == 7
    assert spec["rollout"]["privateKeys"] == "pending-approved-credential-intake"
    assert spec["rollout"]["effectivePermissionReadback"] == "pending"
    connector = spec["githubConnector"]
    assert connector["verifiedOrganizationInstallation"]["organization"] == "re8ch"
    assert connector["verifiedOrganizationInstallation"]["installationId"] == 167331919
    assert "does not inherit" in connector["boundary"]
