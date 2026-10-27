from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / 'clusters/re8ch/components/ops/github-actions-cloud-domains-executor-rbac.yaml'


def test_cloud_domain_adoption_is_named_and_non_destructive():
    docs = list(yaml.safe_load_all(PATH.read_text()))
    states = [d for d in docs if d['kind'] == 'ConfigMap']
    assert len(states) == 75
    for d in docs:
        if d['kind'] == 'ConfigMap':
            assert 'data' not in d
            assert d['metadata']['annotations']['kustomize.toolkit.fluxcd.io/ssa'] == 'IfNotPresent'
        if d['kind'] in {'Role', 'ClusterRole'}:
            for rule in d['rules']:
                assert rule['resourceNames'] and '*' not in rule['resourceNames']
                assert set(rule['verbs']) <= {'get', 'patch', 'update'}
                assert not set(rule['resources']) & {'secrets', 'roles', 'rolebindings', 'clusterroles', 'clusterrolebindings', 'jobs'}
        if d['kind'] == 'RoleBinding':
            assert d['roleRef']['kind'] == 'Role'
            assert all(s['kind'] == 'Group' and s['name'].startswith('re8ch:release-executor:') for s in d['subjects'])


def test_restricted_helm_cluster_access_only_covers_existing_fabric_identities():
    docs = list(yaml.safe_load_all(PATH.read_text()))
    roles = [d for d in docs if d['kind'] == 'ClusterRole']
    assert len(roles) == 1
    assert roles[0]['metadata']['name'] == 'networking-helm-advanced-fabric-kube-system'
    assert roles[0]['rules'] == [
        {'apiGroups': ['networking.k8s.io'], 'resources': ['ingressclasses'], 'resourceNames': ['advanced-fabric'], 'verbs': ['get', 'patch']},
        {'apiGroups': ['networking.advfab.org'], 'resources': ['advancedfabrics'], 'resourceNames': ['advanced-fabric'], 'verbs': ['get', 'patch']},
    ]
    bindings = [d for d in docs if d['kind'] == 'ClusterRoleBinding']
    assert len(bindings) == 1
    assert bindings[0]['roleRef'] == {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'ClusterRole', 'name': roles[0]['metadata']['name']}
    assert bindings[0]['subjects'] == [{'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Group', 'name': 're8ch:release-executor:networking'}]
