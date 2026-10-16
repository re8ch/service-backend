from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DOMAINS = ROOT / "clusters/re8ch/domains"
RECONCILERS = ROOT / "clusters/re8ch/flux-system/domain-reconcilers.yaml"


def load_all(path: Path) -> list[dict]:
    return [item for item in yaml.safe_load_all(path.read_text()) if isinstance(item, dict)]


def test_every_domain_has_one_independent_reconciler():
    domain_names = sorted(path.name for path in DOMAINS.iterdir() if path.is_dir())
    reconcilers = load_all(RECONCILERS)

    assert len(reconcilers) == len(domain_names)
    assert sorted(item["metadata"]["name"].removeprefix("reconcile-") for item in reconcilers) == domain_names
    for item in reconcilers:
        spec = item["spec"]
        domain = item["metadata"]["name"].removeprefix("reconcile-")
        assert spec["path"] == f"./clusters/re8ch/domains/{domain}"
        assert spec["sourceRef"] == {"kind": "GitRepository", "name": "service-backend"}
        assert spec["prune"] is True
        assert spec["wait"] is False
        assert "dependsOn" not in spec


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
