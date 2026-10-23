from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DISPATCH = ROOT / "operations/migrations/github-first-control-plane/static-release-domain-dispatch.yaml"


def load_dispatch():
    return yaml.safe_load(DISPATCH.read_text())


def test_dispatch_matrix_covers_four_autonomous_domains_and_five_main_splits():
    document = load_dispatch()
    items = document["dispatches"]
    autonomous = [item for item in items if item["scope"] == "existing-autonomous-domain"]
    main = [item for item in items if item["scope"] == "split-main-domain"]
    assert {item["authorityDomain"] for item in autonomous} == {
        "agent-runtime",
        "identity-access",
        "networking",
        "observability-metering",
    }
    assert {item["authorityDomain"] for item in main} == {
        "commerce-billing",
        "data-storage",
        "delivery-products",
        "operations-ui",
        "tenant-runtime",
    }
    assert sum(item["staticCohort"]["releases"] for item in main) == 19
    assert sum(item["staticCohort"]["objects"] for item in main) == 142


def test_every_domain_dispatch_inherits_the_cutover_safety_gates():
    document = load_dispatch()
    required = {
        "deterministic-signed-bundle",
        "domain-specific-oidc-and-minimal-rbac",
        "drift-recovery-proven",
        "rollback-policy-proven",
        "consumer-count-zero-before-controller-removal",
    }
    domains = [item for item in document["dispatches"] if item["scope"] != "central-cleanup"]
    assert domains
    for item in domains:
        assert required <= set(item["gates"]), item["id"]


def test_dispatch_references_and_handoff_exist():
    document = load_dispatch()
    for path in document["references"].values():
        assert (ROOT / path).is_file(), path
    handoff = (DISPATCH.parent / "static-release-domain-handoff.md").read_text()
    assert "rook-ceph-9cd3f4260b72b501" in handoff
    assert "40026749978" in handoff
    assert "c691ff19e5d368008556c4274c749ab2f3510843551b8b7a3b31becc7954eda9" in handoff
