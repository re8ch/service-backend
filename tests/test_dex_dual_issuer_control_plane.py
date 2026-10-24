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
    ]
    expressions = {item['expression'] for item in github['claimValidationRules']}
    assert {
        "claims.repository_visibility == 'private'",
        "claims.runner_environment == 'github-hosted'",
    } <= expressions
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
        'agent-runtime': ('re8ch-ai/agent-gitops', '1423311998', '334604670'),
        'identity-access': ('re8ch/identity-gitops', '1423312101', '283958313'),
        'observability-metering': ('siren-observer/observability-gitops', '1423312205', '334604879'),
        'networking': ('pillars-infra/network-gitops', '1423312303', '334604858'),
    }
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
    assert aliases['db-rw.re8ch.com'] == '192.0.2.209'


def test_mubao_k3s_start_gate_keeps_oidc_discovery_on_internal_gateway():
    gate = (
        ROOT.parents[3] / 'operations/network/scripts/mubao-k3s-start-gate.sh'
    ).read_text()
    assert '192.0.2.209 db-rw.re8ch.com' in gate
    assert 'OIDC discovery via authoritative Cilium Gateway' in gate
