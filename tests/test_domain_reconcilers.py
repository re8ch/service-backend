from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DOMAINS = ROOT / "clusters/re8ch/domains"
RECONCILERS = ROOT / "clusters/re8ch/flux-system/domain-reconcilers.yaml"
AUTHORITY = ROOT / "operations/releases/runtime-authority.yaml"
FEDERATION_AUTHORITY = ROOT / "operations/flux-federation/authority.yaml"
FEDERATION_REGISTRATIONS = ROOT / "clusters/re8ch/flux-federation/registrations.yaml"
FEDERATION_CONTROLLERS = ROOT / "clusters/re8ch/flux-federation/controllers.yaml"


def load_all(path: Path) -> list[dict]:
    return [item for item in yaml.safe_load_all(path.read_text()) if isinstance(item, dict)]


def test_every_domain_has_one_independent_reconciler():
    domain_names = sorted(path.name for path in DOMAINS.iterdir() if path.is_dir())
    reconcilers = load_all(RECONCILERS)
    legacy = {item["metadata"]["name"].removeprefix("reconcile-"): item for item in reconcilers}
    federation = yaml.safe_load(FEDERATION_AUTHORITY.read_text())["spec"]["domains"]
    registrations = {
        item["metadata"]["namespace"]: item
        for item in load_all(FEDERATION_REGISTRATIONS)
        if item.get("kind") == "Kustomization"
    }

    assert set(legacy) | set(federation) == set(domain_names)
    for domain, item in legacy.items():
        spec = item["spec"]
        assert spec["path"] == f"./clusters/re8ch/domains/{domain}"
        assert spec["sourceRef"] == {"kind": "GitRepository", "name": "service-backend"}
        assert spec["prune"] is True
        assert spec["wait"] is False
        assert "dependsOn" not in spec
    for domain, authority in federation.items():
        state = authority["state"]
        autonomous = registrations[authority["controllerNamespace"]]["spec"]
        if state == "staged":
            assert domain in legacy and legacy[domain]["spec"].get("suspend", False) is False
            assert autonomous["suspend"] is True and autonomous["prune"] is False
        elif state == "cutover-verification":
            assert legacy[domain]["spec"]["suspend"] is True
            assert autonomous["suspend"] is False and autonomous["prune"] is False
        else:
            assert state == "autonomous"
            assert domain not in legacy
            assert autonomous["suspend"] is False and autonomous["prune"] is True


def test_domain_bundles_are_local_and_secrets_have_one_owner():
    secret_owners: dict[str, str] = {}
    object_owners: dict[tuple[str, str, str, str], str] = {}
    for domain in sorted(DOMAINS.iterdir()):
        if not domain.is_dir():
            continue
        runtime = yaml.safe_load((domain / "kustomization.yaml").read_text())
        assert runtime["resources"] == ["resources.yaml"]
        assert (domain / "resources.yaml").is_file()
        for item in load_all(domain / "resources.yaml"):
            metadata = item.get("metadata", {})
            identity = (
                item.get("apiVersion", ""), item.get("kind", ""),
                metadata.get("namespace", ""), metadata.get("name", ""),
            )
            assert identity not in object_owners, (
                f"object {identity} belongs to both {object_owners.get(identity)} and {domain.name}"
            )
            object_owners[identity] = domain.name

        source = yaml.safe_load((domain / "source/kustomization.yaml").read_text())
        for resource in source.get("resources", []):
            if not resource.endswith(".sops.yaml"):
                continue
            assert resource not in secret_owners, (
                f"encrypted secret {resource} belongs to both {secret_owners[resource]} and {domain.name}"
            )
            secret_owners[resource] = domain.name

    assert secret_owners, "domain secret ownership inventory must not be empty"
    assert len(object_owners) > 1000, "domain inventory unexpectedly incomplete"


def test_root_reconcile_stays_non_pruning_during_ownership_handoff():
    sync = load_all(ROOT / "clusters/re8ch/flux-system/gotk-sync.yaml")
    root = next(item for item in sync if item.get("kind") == "Kustomization")
    assert root["metadata"]["name"] == "service-backend"
    assert root["spec"]["prune"] is False


def test_identity_controller_can_bind_shared_mailbox_role():
    identity_role = next(
        item for item in load_all(FEDERATION_CONTROLLERS)
        if item.get("kind") == "ClusterRole"
        and item["metadata"]["name"] == "flux-identity-access-namespaced"
    )
    bind_rule = next(
        rule for rule in identity_role["rules"]
        if rule.get("apiGroups") == ["rbac.authorization.k8s.io"]
        and rule.get("resources") == ["roles"]
        and "bind" in rule.get("verbs", [])
    )
    assert "mailbox-provisioner" in bind_rule["resourceNames"]


def test_external_fast_changing_products_target_product_runtime():
    policy = yaml.safe_load(AUTHORITY.read_text())
    assert policy["selectionPolicy"]["externalAndFastChanging"] == "product-runtime"
    authorities = policy["authorities"]
    assert set(authorities) == {"commerce-billing", "identity", "connectool"}
    for name, authority in authorities.items():
        assert authority["externalUsers"] is True, name
        assert authority["changeCadence"] == "high", name
        assert authority["targetAuthority"] == "product-runtime", name
        assert authority["cutoverGate"] == "authenticated product release publisher", name
        assert authority["productRepositories"], name
        assert authority["productOwned"], name
        assert authority["fluxOwned"], name


def test_flux_handoff_is_fail_closed_until_publisher_exists():
    policy = yaml.safe_load(AUTHORITY.read_text())
    for name, authority in policy["authorities"].items():
        if authority["currentAuthority"] in {"flux", "split"}:
            assert authority["migrationPhase"] in {"publisher-required", "business-api-live"}, name
            assert authority["cutoverGate"] == "authenticated product release publisher", name
        elif authority["currentAuthority"] == "control-plane-direct":
            assert authority["migrationPhase"] == "flux-detached", name
            assert authority["cutoverGate"] == "authenticated product release publisher", name
        else:
            assert authority["currentAuthority"] == "product-runtime", name
            assert authority["migrationPhase"] == "complete", name


def object_identities(domain: str) -> set[tuple[str, str, str, str]]:
    return {
        (
            item.get("apiVersion", ""),
            item.get("kind", ""),
            item.get("metadata", {}).get("namespace", ""),
            item.get("metadata", {}).get("name", ""),
        )
        for item in load_all(DOMAINS / domain / "resources.yaml")
    }


def test_product_runtime_objects_are_absent_from_flux_bundles():
    excluded = {
        "commerce-billing": {
            ("apps/v1", "Deployment", "payments", "commerce-api"),
            ("batch/v1", "CronJob", "payments", "commerce-wechat-reconcile"),
            ("apps/v1", "Deployment", "payments", "nuclio-payment-registry-api"),
            ("v1", "ConfigMap", "payments", "nuclio-payment-registry-api"),
        },
        "identity-access": {
            ("apps/v1", "Deployment", "auth", "re8ch-auth-gateway"),
            ("policy/v1", "PodDisruptionBudget", "auth", "re8ch-auth-gateway"),
            ("apps/v1", "Deployment", "auth", "re8ch-user-center"),
        },
        "agent-runtime": {
            ("source.toolkit.fluxcd.io/v1", "OCIRepository", "flux-system", "connectool"),
            ("helm.toolkit.fluxcd.io/v2", "HelmRelease", "flux-system", "connectool"),
        },
    }
    for domain, forbidden in excluded.items():
        assert object_identities(domain).isdisjoint(forbidden), domain


def test_flux_keeps_platform_edges_for_detached_products():
    required = {
        "commerce-billing": {
            ("v1", "Service", "payments", "commerce-api"),
            ("gateway.networking.k8s.io/v1", "HTTPRoute", "payments", "commerce-public-api"),
            ("v1", "Service", "payments", "nuclio-payment-registry-api"),
        },
        "identity-access": {
            ("v1", "Service", "auth", "re8ch-auth-gateway"),
            ("v1", "Service", "auth", "re8ch-user-center"),
            ("gateway.networking.k8s.io/v1", "HTTPRoute", "auth", "re8ch-user-center"),
        },
        "agent-runtime": {
            ("gateway.networking.k8s.io/v1", "HTTPRoute", "toolhive-system", "connectool-cloud-mcp"),
        },
    }
    for domain, expected in required.items():
        assert expected <= object_identities(domain), domain
