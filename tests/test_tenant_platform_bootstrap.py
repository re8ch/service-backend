import pathlib, yaml

ROOT=pathlib.Path(__file__).parents[1]

def docs(path): return [d for d in yaml.safe_load_all((ROOT/path).read_text()) if d]

def test_bootstrap_workflow_is_exact_and_does_not_use_flux_or_secret_reads():
    text=(ROOT/'.github/workflows/tenant-platform-bootstrap.yml').read_text()
    assert 'platform-bootstrap-tenant-infrastructure' in text
    assert 'ghcr.io/8iops/agent-gitops-release/agent-runtime-domain-tenant-infrastructure' in text
    assert 'cosign verify' in text and 'oras pull' in text
    assert 'flux ' not in text and 'kubectl exec' not in text
    assert 'get secrets' in text and '= no' in text
    assert 'create clusterroles' in text and 'impersonate users' in text

def test_bootstrap_access_has_admission_name_guard_and_no_escalation():
    items=docs('operations/releases/tenant-platform-bootstrap-access.yaml')
    kinds={d['kind'] for d in items}
    assert kinds == {'ValidatingAdmissionPolicy','ValidatingAdmissionPolicyBinding','Role','RoleBinding'}
    role=next(d for d in items if d['kind']=='Role')
    verbs={v for rule in role['rules'] for v in rule['verbs']}
    resources={v for rule in role['rules'] for v in rule['resources']}
    assert {'bind','escalate','impersonate','delete','deletecollection'}.isdisjoint(verbs)
    assert 'secrets' not in resources and 'clusterroles' not in resources
    policy=next(d for d in items if d['kind']=='ValidatingAdmissionPolicy')
    expression=policy['spec']['validations'][0]['expression']
    assert "object.metadata.namespace == 'tenant-infrastructure'" in expression
    assert "'tenant-api'" in expression and "'tenant-native-mcp'" in expression

def test_authentication_configuration_binds_only_exact_workflow():
    text=(ROOT/'clusters/re8ch/components/ops/dex-dual-issuer-authentication.yaml').read_text()
    assert 'https://kube-api.re8ch.com/platform-bootstrap-tenant-infrastructure' in text
    assert "claims.workflow_ref == 're8ch/service-backend/.github/workflows/tenant-platform-bootstrap.yml@refs/heads/main'" in text
    assert "claims.environment == 'platform-bootstrap-tenant-infrastructure'" in text
    assert "['re8ch:platform-bootstrap:tenant-infrastructure']" in text
