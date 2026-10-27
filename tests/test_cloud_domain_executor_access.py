from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / 'clusters/re8ch/components/ops/github-actions-cloud-domains-executor-rbac.yaml'


def test_cloud_domain_adoption_is_named_and_non_destructive():
    docs = list(yaml.safe_load_all(PATH.read_text()))
    states = [d for d in docs if d['kind'] == 'ConfigMap']
    assert len(states) == 77
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


def test_networking_topology_diagnostic_is_exact_read_only_platform_owned():
    import json
    path=ROOT/'clusters/re8ch/components/ops/github-actions-networking-topology-read.yaml'
    role,binding=list(yaml.safe_load_all(path.read_text()))
    assert role['kind']=='Role' and role['metadata']['namespace']=='ops'
    assert role['rules']==[{'apiGroups':[''],'resources':['configmaps'],'resourceNames':['restricted-topology-context'],'verbs':['get']}]
    assert binding['kind']=='RoleBinding' and binding['metadata']['namespace']=='ops'
    assert binding['roleRef']=={'apiGroup':'rbac.authorization.k8s.io','kind':'Role','name':role['metadata']['name']}
    assert binding['subjects']==[{'apiGroup':'rbac.authorization.k8s.io','kind':'Group','name':'re8ch:release-executor:networking'}]
    ledger=json.loads((ROOT/'operations/releases/cloud-domain-access-sources.json').read_text())
    diagnostic=ledger['platformDiagnosticAccess'][0]
    assert diagnostic['owner']=='re8ch/service-backend' and diagnostic['verbs']==['get']
    assert diagnostic['resourceNames']==['restricted-topology-context']


def test_transferred_domain_authority_has_no_old_writer_overlap():
    docs = list(yaml.safe_load_all(PATH.read_text()))
    roles = {d['metadata']['name']: d for d in docs if d['kind'] == 'Role'}
    old_mail = roles['identity-access-domain-mail-system']
    assert all(r['verbs'] == ['get'] for r in old_mail['rules'])
    assert roles['agent-runtime-domain-mail-system']['metadata']['namespace'] == 'mail-system'
    def writes(role):
        return {(g, resource, name) for r in role['rules'] if 'patch' in r['verbs']
                for g in r['apiGroups'] for resource in r['resources'] for name in r['resourceNames']}
    grafana = writes(roles['observability-domain-grafana'])
    assert ('apps', 'deployments', 're8ch-grafana') in grafana
    assert not grafana & writes(roles['observability-domain-observability-system'])
    edge = writes(roles['networking-helm-public-edge-manager-regional-routing'])
    for name in ('public-edge-nameservers', 'public-edge-runtime'):
        assert ('', 'configmaps', name) not in edge
    for name in ('public-edge-manager', 'public-edge-manager-records'):
        assert ('', 'configmaps', name) in edge
