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
    assert github['issuer']['audiences'] == ['https://kube-api.re8ch.com/observer']
    expressions = {item['expression'] for item in github['claimValidationRules']}
    assert {
        "claims.repository_id == '1367237854'",
        "claims.repository_owner_id == '283958313'",
        "claims.ref == 'refs/heads/main'",
        "claims.environment == 'cluster-observation'",
        "claims.runner_environment == 'github-hosted'",
    } <= expressions
    assert github['claimMappings']['username']['expression'] == "'github-actions:node-observer:' + claims.run_id"
    assert github['claimMappings']['groups']['expression'] == "['re8ch:credential-contract:node-observer']"
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
