"""Retire local GitOps writers without removing their workloads or history."""
CONTROLLERS = {'source-controller', 'kustomize-controller', 'helm-controller',
               'notification-controller', 'image-reflector-controller', 'image-automation-controller'}


def test_all_five_flux_stacks_are_dormant(kustomize_documents):
    docs = kustomize_documents('clusters/re8ch')
    controllers = [d for d in docs if d.get('kind') == 'Deployment'
                   and d['metadata']['name'] in CONTROLLERS
                   and d['metadata'].get('namespace', '').startswith('flux-')]
    assert {d['metadata']['namespace'] for d in controllers} == {
        'flux-system', 'flux-agent-system', 'flux-identity-system',
        'flux-network-system', 'flux-observability-system'}
    assert all(d['spec']['replicas'] == 0 for d in controllers)
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
