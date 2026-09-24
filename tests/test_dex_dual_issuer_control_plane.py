from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1] / 'clusters/re8ch/components/ops'


def test_all_control_planes_share_dual_issuer_authentication():
    auth = yaml.safe_load((ROOT / 'dex-dual-issuer-authentication.yaml').read_text())
    assert auth['apiVersion'] == 'apiserver.config.k8s.io/v1'
    assert auth['kind'] == 'AuthenticationConfiguration'
    assert [item['issuer']['url'] for item in auth['jwt']] == [
        'https://dex.re8ch.com', 'https://dex.service.re8ch.com'
    ]
    for item in auth['jwt']:
        assert item['issuer']['audiences'] == ['headlamp']
        assert item['claimMappings']['username'] == {'claim': 'email', 'prefix': 'oidc:'}
        assert item['claimMappings']['groups'] == {'claim': 'groups', 'prefix': 'oidc:'}
    for node in ('restricted-node-6dc3f140f9', 'a1-wsl2', 'b1-wsl2'):
        config = yaml.safe_load((ROOT / f'{node}-k3s-server-config.yaml').read_text())
        flags = config['kube-apiserver-arg']
        assert 'authentication-config=/etc/rancher/k3s/dex-dual-issuer-authentication.yaml' in flags
        assert not any(flag.startswith('oidc-') for flag in flags)


def test_headlamp_uses_api_trusted_canonical_issuer_and_service_callback():
    deployment = yaml.safe_load((ROOT.parents[3] / 'platform/headlamp/deployment.yaml').read_text())
    pod = deployment['spec']['template']['spec']
    container = next(item for item in pod['containers'] if item['name'] == 'headlamp')
    assert '-oidc-idp-issuer-url=https://dex.re8ch.com' in container['args']
    assert '-oidc-callback-url=https://headlamp.service.re8ch.com/oidc-callback' in container['args']
    aliases = {hostname: item['ip'] for item in pod['hostAliases'] for hostname in item['hostnames']}
    assert aliases['dex.service.re8ch.com'] == '192.0.2.213'
