from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1] / 'clusters/re8ch/components/ops'


def test_all_control_planes_share_supabase_authentication():
    auth = yaml.safe_load((ROOT / 'dex-dual-issuer-authentication.yaml').read_text())
    assert auth['apiVersion'] == 'apiserver.config.k8s.io/v1'
    assert auth['kind'] == 'AuthenticationConfiguration'
    assert [item['issuer']['url'] for item in auth['jwt']] == [
        'https://db-rw.re8ch.com/auth/v1',
        'https://token.actions.githubusercontent.com',
    ]
    headlamp_supabase = auth['jwt'][0]
    assert headlamp_supabase['issuer']['audiences'] == ['authenticated']
    assert headlamp_supabase['claimValidationRules'] == [{
        'expression': "claims.client_id == 'ed4e0331-7522-59f8-8a72-2d558844dc24'",
        'message': 'token must have been issued to the Headlamp OAuth client',
    }]
    assert headlamp_supabase['claimMappings'] == {
        'username': {'claim': 'email', 'prefix': 'oidc:'}
    }
    github = auth['jwt'][1]
    assert github['issuer']['audiences'] == [
        'https://kube-api.re8ch.com/observer',
        'https://kube-api.re8ch.com/release-executor-api-conformance',
        'https://kube-api.re8ch.com/release-executor-agent-runtime',
        'https://kube-api.re8ch.com/release-executor-identity-access',
        'https://kube-api.re8ch.com/release-executor-observability-metering',
        'https://kube-api.re8ch.com/release-executor-networking',
        'https://kube-api.re8ch.com/release-executor-data-storage',
        'https://kube-api.re8ch.com/release-executor-commerce-billing',
        'https://kube-api.re8ch.com/release-executor-delivery-products',
        'https://kube-api.re8ch.com/release-executor-operations-ui',
        'https://kube-api.re8ch.com/release-executor-tenant-runtime',
        'https://kube-api.re8ch.com/release-executor-ceph-infra',
        'https://kube-api.re8ch.com/release-executor-ceph-runtime',
        'https://kube-api.re8ch.com/release-artifact-publisher',
        'https://kube-api.re8ch.com/release-reconciler-data-storage',
        'https://kube-api.re8ch.com/release-reconciler-commerce-billing',
        'https://kube-api.re8ch.com/release-reconciler-operations-ui',
        'https://kube-api.re8ch.com/release-reconciler-tenant-runtime',
        'https://kube-api.re8ch.com/release-reconciler-networking',
        'https://kube-api.re8ch.com/release-reconciler-delivery-products',
    ]
    expressions = {' '.join(item['expression'].split()) for item in github['claimValidationRules']}
    public_rule, runner_rule = github['claimValidationRules'][:2]
    for rule in (public_rule, runner_rule):
        expression = ' '.join(rule['expression'].split())
        for boundary in (
            "claims.repository_id == '1367237854'",
            "claims.repository_owner_id == '283958313'",
            "claims.repository == 're8ch/service-backend'",
            "claims.ref == 'refs/heads/main'",
            "claims.workflow_ref == 're8ch/service-backend/.github/workflows/main-domain-release.yml@refs/heads/main'",
            "claims.event_name == 'workflow_dispatch'",
            "claims.environment in ['release-executor-data-storage', 'release-executor-commerce-billing', 'release-executor-operations-ui', 'release-executor-tenant-runtime']",
            "claims.aud == 'https://kube-api.re8ch.com/' + claims.environment",
            "claims.workflow_ref == 're8ch/service-backend/.github/workflows/release-compat-controller.yml@refs/heads/main'",
            "claims.environment == 'release-ceph-compiler'",
            "claims.aud == 'https://kube-api.re8ch.com/release-artifact-publisher'",
        ):
            assert boundary in expression
    assert "claims.runner_environment == 'github-hosted'" in runner_rule['expression']
    assert "claims.runner_environment == 'self-hosted' ||" in runner_rule['expression']
    assert "claims.repository_visibility == 'private' ||" in public_rule['expression']
    route = github['claimValidationRules'][-1]['expression']
    for required in (
        "claims.aud == 'https://kube-api.re8ch.com/observer'",
        "claims.aud == 'https://kube-api.re8ch.com/release-executor-api-conformance'",
        "claims.workflow_ref == 're8ch/service-backend/.github/workflows/unified-release.yml@refs/heads/main'",
        "claims.environment == 'release-api-conformance'",
        "claims.workflow_ref == 're8ch/service-backend/.github/workflows/main-domain-release.yml@refs/heads/main'",
        "claims.aud == 'https://kube-api.re8ch.com/release-artifact-publisher'",
        "re8ch/service-backend/.github/workflows/release-compat-controller.yml@refs/heads/main",
        "re8ch/service-backend/.github/workflows/ceph-shadow-compile.yml@refs/heads/main",
        "claims.environment == 'release-ceph-compiler'",
        "claims.event_name == 'workflow_dispatch'",
    ):
        assert required in route
    assert "github-actions:release-executor-api-conformance:" in github['claimMappings']['username']['expression']
    assert "re8ch:release-executor:api-conformance" in github['claimMappings']['groups']['expression']
    external = {
        'delivery-products': ('re8ch/delivery-products-gitops', '1431915128', '283958313'),
        'agent-runtime': ('8iops/agent-gitops', '1435935941', '334604670'),
        'identity-access': ('re8ch/identity-gitops', '1423312101', '283958313'),
        'observability-metering': ('siren-observer/observability-gitops', '1423312205', '334604879'),
        'networking': ('pillars-infra/network-gitops', '1435935851', '334604858'),
    }
    for domain in ('agent-runtime', 'identity-access', 'networking'):
        repository, repository_id, owner_id = external[domain]
        for boundary_rule in (public_rule, runner_rule):
            expr = boundary_rule['expression']
            for expected in (
                f"claims.repository_id == '{repository_id}'",
                f"claims.repository_owner_id == '{owner_id}'",
                f"claims.repository == '{repository}'",
                f"claims.workflow_ref == '{repository}/.github/workflows/static-release.yml@refs/heads/main'",
                f"claims.environment == 'release-executor-{domain}'",
                f"claims.aud == 'https://kube-api.re8ch.com/release-executor-{domain}'",
            ):
                assert expected in expr
    for domain, (repository, repository_id, owner_id) in external.items():
        assert f"claims.repository_id == '{repository_id}'" in route
        assert f"claims.repository_owner_id == '{owner_id}'" in route
        assert f"claims.repository == '{repository}'" in route
        assert f"claims.aud == 'https://kube-api.re8ch.com/release-executor-{domain}'" in route
        assert f"claims.workflow_ref == '{repository}/.github/workflows/static-release.yml@refs/heads/main'" in route
        assert f"claims.environment == 'release-executor-{domain}'" in route
        assert f"github-actions:release-executor-{domain}:" in github['claimMappings']['username']['expression']
        assert f"re8ch:release-executor:{domain}" in github['claimMappings']['groups']['expression']
    for domain in ('data-storage', 'commerce-billing', 'operations-ui', 'tenant-runtime'):
        assert f"claims.aud == 'https://kube-api.re8ch.com/release-executor-{domain}'" in route
        assert f"claims.environment == 'release-executor-{domain}'" in route
        assert f"github-actions:release-executor-{domain}:" in github['claimMappings']['username']['expression']
        assert f"re8ch:release-executor:{domain}" in github['claimMappings']['groups']['expression']
    for expected in (
        "claims.repository_id == '1440886799'",
        "claims.repository_owner_id == '334604832'",
        "claims.repository == 'strata-silica/storage-gitops'",
        "strata-silica/storage-gitops/.github/workflows/storage-static-release.yml@refs/heads/main",
        "strata-silica/storage-gitops/.github/workflows/storage-reconcile.yml@refs/heads/main",
        "strata-silica/storage-gitops/.github/workflows/ceph-static-release.yml@refs/heads/main",
        "strata-silica/storage-gitops/.github/workflows/ceph-shadow-compile.yml@refs/heads/main",
        "claims.aud == 'https://kube-api.re8ch.com/release-reconciler-data-storage'",
    ):
        assert expected in route
    assert "github-actions:release-artifact-publisher:" in github['claimMappings']['username']['expression']
    assert "re8ch:release-artifact-publisher" in github['claimMappings']['groups']['expression']
    configs = [ROOT / f'{node}-k3s-server-config.yaml' for node in ('a1-wsl2', 'b1-wsl2')]
    configs.append(ROOT.parents[3] / 'operations/network/manifests/network/private-transit/mubao-k3s-config.yaml')
    for path in configs:
        config = yaml.safe_load(path.read_text())
        flags = config['kube-apiserver-arg']
        assert 'authentication-config=/etc/rancher/k3s/dex-dual-issuer-authentication.yaml' in flags
        assert not any(flag.startswith('oidc-') for flag in flags)


def test_headlamp_uses_api_trusted_supabase_issuer_and_service_callback():
    deployment = yaml.safe_load((ROOT.parents[3] / 'platform/headlamp/deployment.yaml').read_text())
    pod = deployment['spec']['template']['spec']
    container = next(item for item in pod['containers'] if item['name'] == 'headlamp')
    assert '-oidc-idp-issuer-url=https://db-rw.re8ch.com/auth/v1' in container['args']
    assert '-oidc-client-id=ed4e0331-7522-59f8-8a72-2d558844dc24' in container['args']
    assert '-oidc-validator-client-id=authenticated' in container['args']
    assert '-oidc-scopes=profile,email,offline_access' in container['args']
    assert '-oidc-use-access-token' in container['args']
    assert '-oidc-callback-url=https://headlamp.service.re8ch.com/oidc-callback' in container['args']
    aliases = {hostname: item['ip'] for item in pod['hostAliases'] for hostname in item['hostnames']}
    assert aliases['db-rw.re8ch.com'] == '10.251.0.4'


def test_mubao_k3s_start_gate_keeps_oidc_discovery_on_internal_gateway():
    gate = (
        ROOT.parents[3] / 'operations/network/scripts/mubao-k3s-start-gate.sh'
    ).read_text()
    assert '10.251.0.4 db-rw.re8ch.com' in gate
    assert 'OIDC discovery via authoritative Cilium Gateway' in gate


def test_replay_identity_is_bound_to_reviewed_hosted_workflow():
    config = yaml.safe_load((ROOT / 'dex-dual-issuer-authentication.yaml').read_text())
    github = next(j for j in config['jwt'] if j['issuer']['url'] == 'https://token.actions.githubusercontent.com')
    for rule in github['claimValidationRules']:
        route = next(part for part in rule['expression'].split('|| (claims.repository_visibility')
                     if 'main-domain-reconcile.yml' in part)
        assert "claims.workflow_ref == 're8ch/service-backend/.github/workflows/main-domain-reconcile.yml@refs/heads/main'" in route
        assert "claims.repository_id == '1367237854'" in route
        assert "claims.ref == 'refs/heads/main'" in route
        assert "claims.aud == 'https://kube-api.re8ch.com/' + claims.environment" in route
        assert "claims.repository_owner_id == '283958313'" in route
        assert "claims.runner_environment == 'github-hosted'" in route
        assert "claims.event_name in ['workflow_dispatch', 'schedule', 'push']" in route
    for domain in ('data-storage', 'commerce-billing', 'operations-ui', 'tenant-runtime'):
        audience = 'https://kube-api.re8ch.com/release-reconciler-' + domain
        assert audience in github['issuer']['audiences']
        assert audience in github['claimMappings']['groups']['expression']


def test_network_replay_has_exact_identity_and_existing_permission_group():
    github = yaml.safe_load((ROOT / 'dex-dual-issuer-authentication.yaml').read_text())['jwt'][1]
    for rule in github['claimValidationRules']:
        branches = rule['expression'].split('|| (claims.repository_visibility')
        branch = next(b for b in branches if 'networking-reconcile.yml' in b)
        for constraint in [
            "claims.repository_id == '1435935851'",
            "claims.repository_owner_id == '334604858'",
            "claims.repository == 'pillars-infra/network-gitops'",
            "claims.ref == 'refs/heads/main'",
            "claims.runner_environment == 'github-hosted'",
            "claims.workflow_ref == 'pillars-infra/network-gitops/.github/workflows/networking-reconcile.yml@refs/heads/main'",
            "claims.environment == 'release-reconciler-networking'",
            "claims.aud == 'https://kube-api.re8ch.com/release-reconciler-networking'",
            "claims.event_name in ['workflow_dispatch', 'schedule', 'push']",
        ]:
            assert constraint in branch
        assert 'static-release.yml' not in branch
    assert "'github-actions:release-reconciler-networking:' + claims.run_id" in github['claimMappings']['username']['expression']
    assert "claims.aud == 'https://kube-api.re8ch.com/release-reconciler-networking' ? ['re8ch:release-executor:networking']" in github['claimMappings']['groups']['expression']


def test_delivery_replay_private_runner_claims_are_exact():
    from types import SimpleNamespace
    github = yaml.safe_load((ROOT / 'dex-dual-issuer-authentication.yaml').read_text())['jwt'][1]
    routes = github['claimValidationRules'][-1]['expression'].split('|| (claims.repository_visibility')
    branch = next(part for part in routes if 'delivery-products-reconcile.yml' in part)
    expression = ('claims.repository_visibility' + branch).strip().removesuffix(')').replace('&&', ' and ')
    expression = ' '.join(expression.split())
    values = dict(repository_visibility='private', repository_id='1431915128', repository_owner_id='283958313',
                  repository='re8ch/delivery-products-gitops', ref='refs/heads/main', runner_environment='self-hosted',
                  workflow_ref='re8ch/delivery-products-gitops/.github/workflows/delivery-products-reconcile.yml@refs/heads/main',
                  event_name='schedule', environment='release-reconciler-delivery-products',
                  aud='https://kube-api.re8ch.com/release-reconciler-delivery-products')
    def accepted(claims):
        return eval(expression, {'__builtins__': {}}, {'claims': SimpleNamespace(**claims)})
    assert accepted(values)
    for key in values:
        assert not accepted({**values, key: 'untrusted'})
    for event in ('workflow_dispatch', 'schedule', 'push'):
        assert accepted({**values, 'event_name': event})
    for event in ('pull_request', 'pull_request_target', 'repository_dispatch'):
        assert not accepted({**values, 'event_name': event})
    assert "claims.aud == 'https://kube-api.re8ch.com/release-reconciler-delivery-products' ? ['re8ch:release-executor:delivery-products']" in github['claimMappings']['groups']['expression']


def test_ceph_cloud_runner_exception_keeps_each_exact_claim_and_environment_audience_pair():
    from types import SimpleNamespace
    github = yaml.safe_load((ROOT / 'dex-dual-issuer-authentication.yaml').read_text())['jwt'][1]
    for rule in github['claimValidationRules'][:2]:
        branch = next(b for b in rule['expression'].split('|| (') if 'ceph-static-release.yml' in b)
        expression = branch.strip().removesuffix(')').replace('&&', ' and ')
        expression = ' '.join(expression.split())
        values = dict(repository_visibility='public', repository_id='1367237854', repository_owner_id='283958313',
                      repository='re8ch/service-backend', ref='refs/heads/main', runner_environment='github-hosted',
                      workflow_ref='re8ch/service-backend/.github/workflows/ceph-static-release.yml@refs/heads/main',
                      event_name='workflow_dispatch', environment='release-executor-ceph-infra',
                      aud='https://kube-api.re8ch.com/release-executor-ceph-infra')
        def accepted(claims):
            return eval(expression, {'__builtins__': {}}, {'claims': SimpleNamespace(**claims)})
        assert accepted(values)
        for key in values:
            assert not accepted({**values, key: 'untrusted'})
        assert accepted({**values, 'environment': 'release-executor-ceph-runtime', 'aud': 'https://kube-api.re8ch.com/release-executor-ceph-runtime'})
        assert not accepted({**values, 'environment': 'release-executor-ceph-runtime'})
