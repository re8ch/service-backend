from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1] / 'clusters/re8ch/components/ops'


def test_all_control_planes_share_supabase_authentication():
    auth = yaml.safe_load((ROOT / 'dex-dual-issuer-authentication.yaml').read_text())
    assert auth['apiVersion'] == 'apiserver.config.k8s.io/v1'
    assert auth['kind'] == 'AuthenticationConfiguration'
    assert [item['issuer']['url'] for item in auth['jwt']] == ['https://db-rw.re8ch.com/auth/v1']
    headlamp_supabase = auth['jwt'][0]
    assert headlamp_supabase['issuer']['audiences'] == ['ed4e0331-7522-59f8-8a72-2d558844dc24']
    assert headlamp_supabase['claimMappings'] == {
        'username': {'claim': 'email', 'prefix': 'oidc:'}
    }
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
    assert '-oidc-scopes=profile,email,offline_access' in container['args']
    assert '-oidc-callback-url=https://headlamp.service.re8ch.com/oidc-callback' in container['args']
    aliases = {hostname: item['ip'] for item in pod['hostAliases'] for hostname in item['hostnames']}
    assert aliases['db-rw.re8ch.com'] == '192.0.2.213'
