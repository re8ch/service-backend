from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / 'clusters/re8ch/components/ops/github-actions-cloud-domains-executor-rbac.yaml'


def test_cloud_domain_adoption_is_named_and_non_destructive():
    docs = list(yaml.safe_load_all(PATH.read_text()))
    states = [d for d in docs if d['kind'] == 'ConfigMap']
    assert len(states) == 74
    for d in docs:
        if d['kind'] == 'ConfigMap':
            assert 'data' not in d
            assert d['metadata']['annotations']['kustomize.toolkit.fluxcd.io/ssa'] == 'IfNotPresent'
        if d['kind'] == 'Role':
            for rule in d['rules']:
                assert rule['resourceNames'] and '*' not in rule['resourceNames']
                assert set(rule['verbs']) <= {'get', 'patch', 'update'}
                assert not set(rule['resources']) & {'secrets', 'roles', 'rolebindings', 'clusterroles', 'clusterrolebindings', 'jobs'}
        if d['kind'] == 'RoleBinding':
            assert d['roleRef']['kind'] == 'Role'
            assert all(s['kind'] == 'Group' and s['name'].startswith('re8ch:release-executor:') for s in d['subjects'])
