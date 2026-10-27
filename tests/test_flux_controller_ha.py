from pathlib import Path

import yaml

# LibYAML keeps SafeLoader constructors; pure Python remains a supported fallback.
YAML_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


ROOT = Path(__file__).resolve().parents[1]
CLUSTER = ROOT / "clusters/re8ch"
CORE_CONTROLLERS = {
    "helm-controller",
    "notification-controller",
    "kustomize-controller",
}
DISRUPTION_BUDGETED = {"helm-controller", "notification-controller"}
EDGE_FAILOVER_NODES = ["overseas-kl", "r640"]


def test_replicated_controllers_have_quorum_disruption_budgets():
    budgets = list(yaml.load_all((CLUSTER / "flux-system/controller-ha.yaml").read_text(), Loader=YAML_SAFE_LOADER))
    assert {item["metadata"]["name"] for item in budgets} == DISRUPTION_BUDGETED
    for item in budgets:
        assert item["kind"] == "PodDisruptionBudget"
        assert item["spec"]["minAvailable"] == 1
        assert item["spec"]["selector"]["matchLabels"]["app"] == item["metadata"]["name"]


def test_root_kustomization_applies_ha_overlay_without_editing_generated_flux():
    root = yaml.load((CLUSTER / "kustomization.yaml").read_text(), Loader=YAML_SAFE_LOADER)
    assert "flux-system/controller-ha.yaml" in root["resources"]
    assert any(
        patch.get("target", {}).get("name")
        == "(helm-controller|notification-controller)"
        for patch in root["patches"]
    )


def test_root_kustomization_allows_slow_artifact_delivery():
    sync = list(yaml.load_all((CLUSTER / "flux-system/gotk-sync.yaml").read_text(), Loader=YAML_SAFE_LOADER))
    root = next(item for item in sync if item["kind"] == "Kustomization")
    assert root["spec"]["timeout"] == "20m"


"""Retire local GitOps writers without removing their workloads or history."""
CONTROLLERS = {'source-controller', 'kustomize-controller', 'helm-controller',
               'notification-controller', 'image-reflector-controller', 'image-automation-controller'}


def test_all_five_flux_stacks_are_removed_but_history_retained(kustomize_documents):
    docs = kustomize_documents('clusters/re8ch')
    controllers = [d for d in docs if d.get('kind') == 'Deployment'
                   and d['metadata']['name'] in CONTROLLERS
                   and d['metadata'].get('namespace', '').startswith('flux-')]
    assert controllers == []
    assert any(d.get('kind') == 'CustomResourceDefinition'
               and d['metadata']['name'] == 'helmreleases.helm.toolkit.fluxcd.io' for d in docs)


def test_root_cannot_reactivate_or_prune_legacy_owners(kustomize_documents):
    docs = kustomize_documents('clusters/re8ch')
    owners = [d for d in docs if d.get('apiVersion', '').split('/')[0] in {
        'kustomize.toolkit.fluxcd.io', 'helm.toolkit.fluxcd.io'}
        and d.get('kind') in {'Kustomization', 'HelmRelease'}]
    assert owners
    for d in owners:
        assert d['spec']['suspend'] is True
        if d['kind'] == 'Kustomization':
            assert d['spec']['prune'] is False
    # Flux retirement must not scale unrelated runtime controllers.
    for d in docs:
        if d.get('kind') == 'Deployment' and d['metadata']['name'] not in CONTROLLERS:
            assert d['metadata'].get('namespace') not in {'flux-agent-system', 'flux-network-system', 'flux-identity-system', 'flux-observability-system'} or d['spec'].get('replicas', 1) != 0
