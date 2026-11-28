import pathlib, yaml

ROOT=pathlib.Path(__file__).parents[1]

def docs(path): return [d for d in yaml.safe_load_all((ROOT/path).read_text()) if d]

def test_bootstrap_workflow_is_exact_and_does_not_use_flux_or_secret_reads():
    text=(ROOT/'.github/workflows/tenant-platform-bootstrap.yml').read_text()
    assert 'platform-bootstrap-tenant-infrastructure' in text
    assert 'EXECUTOR_AUDIENCE: https://kube-api.re8ch.com/release-executor-tenant-runtime' in text
    assert 'ghcr.io/8iops/agent-gitops-release/agent-runtime-domain-tenant-infrastructure' in text
    assert 'ghcr.io/8iops/agent-gitops-release/agent-runtime-domain-toolhive-system' in text
    assert 'toolhive_catalog_bootstrap.py' in text
    assert '--access clusters/re8ch/components/ops/github-actions-agent-runtime-executor-rbac.yaml' in text
    assert 'tenant-observation-access' in text and '--role-only' in text
    assert 'cosign verify' in text and 'oras blob fetch' in text
    assert "test \"$(jq '.layers | length' \"$manifest\")\" = 1" in text
    assert 'application/vnd.cncf.flux.content.v1.tar+gzip' in text
    assert 'tar -xzf "$layer" -C "$RUNNER_TEMP/bundle" release.bundle' in text
    assert 'OIDC authorization claims:' in text
    assert '"OIDC_TOKEN"' in text and 'print(token)' not in text
    assert 'flux ' not in text and 'kubectl exec' not in text
    assert 'get secrets' in text and '= no' in text
    assert 'create clusterroles' in text and 'impersonate users' in text
    assert text.count('--force-conflicts') == 2

def test_bootstrap_access_has_exact_admission_and_escalation_guards():
    items=docs('operations/releases/tenant-platform-bootstrap-access.yaml')
    kinds={d['kind'] for d in items}
    assert kinds == {'ValidatingAdmissionPolicy','ValidatingAdmissionPolicyBinding','Role','RoleBinding'}
    roles={d['metadata']['name']:d for d in items if d['kind']=='Role'}
    assert set(roles)=={'github-tenant-platform-bootstrap','github-toolhive-catalog-bootstrap'}
    all_verbs={v for role in roles.values() for rule in role['rules'] for v in rule['verbs']}
    all_resources={v for role in roles.values() for rule in role['rules'] for v in rule['resources']}
    assert {'bind','impersonate','delete','deletecollection'}.isdisjoint(all_verbs)
    assert 'secrets' not in all_resources and 'clusterroles' not in all_resources
    escalation=[rule for rule in roles['github-toolhive-catalog-bootstrap']['rules'] if 'escalate' in rule['verbs']]
    assert escalation == [{'apiGroups':['rbac.authorization.k8s.io'],'resources':['roles'],
                           'resourceNames':['agent-runtime-domain-toolhive-system'],
                           'verbs':['get','patch','update','escalate']}]
    tenant_escalation=[rule for rule in roles['github-tenant-platform-bootstrap']['rules'] if 'escalate' in rule['verbs']]
    assert tenant_escalation == [{'apiGroups':['rbac.authorization.k8s.io'],'resources':['roles'],
                                  'resourceNames':['agent-runtime-domain-tenant-infrastructure'],
                                  'verbs':['get','patch','update','escalate']}]
    policy=next(d for d in items if d['kind']=='ValidatingAdmissionPolicy')
    expression=policy['spec']['validations'][0]['expression']
    assert "object.metadata.namespace == 'tenant-infrastructure'" in expression
    assert "'tenant-api'" in expression and "'tenant-native-mcp'" in expression
    assert "object.metadata.namespace == 'toolhive-system'" in expression
    assert "'agent-runtime-domain-toolhive-system'" in expression
    assert "'re8ch-approved-tool-catalog'" in expression

def test_authentication_configuration_binds_only_exact_workflow():
    text=(ROOT/'clusters/re8ch/components/ops/dex-dual-issuer-authentication.yaml').read_text()
    assert 'https://kube-api.re8ch.com/release-executor-tenant-runtime' in text
    assert "claims.workflow_ref == 're8ch/service-backend/.github/workflows/tenant-platform-bootstrap.yml@refs/heads/main'" in text
    assert "claims.environment == 'platform-bootstrap-tenant-infrastructure'" in text
    assert "['re8ch:platform-bootstrap:tenant-infrastructure']" in text


def test_agent_runtime_observation_access_is_namespace_scoped_and_read_only():
    items=docs('clusters/re8ch/components/ops/github-actions-agent-runtime-executor-rbac.yaml')
    role=next(d for d in items if d['kind']=='Role'
              and d['metadata']['name']=='agent-runtime-domain-tenant-infrastructure')
    assert role['metadata']['namespace']=='tenant-infrastructure'
    unbounded={(tuple(rule['apiGroups']),tuple(rule['resources']),tuple(rule['verbs']))
               for rule in role['rules'] if not rule.get('resourceNames')}
    assert (('',),('pods','events'),('get','list')) in unbounded
    assert (('apps',),('replicasets',),('get','list')) in unbounded
