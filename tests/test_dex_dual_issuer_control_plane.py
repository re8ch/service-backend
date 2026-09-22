from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1] / 'clusters/re8ch/components/ops'


def test_all_control_planes_share_dual_issuer_authentication():
    auth = yaml.safe_load((ROOT / 'dex-dual-issuer-authentication.yaml').read_text())
    assert auth['apiVersion'] == 'apiserver.config.k8s.io/v1'
    assert auth['kind'] == 'AuthenticationConfiguration'
    assert [item['issuer']['url'] for item in auth['jwt']] == [
        'https://dex.service.re8ch.com', 'https://dex.service.re8ch.com'
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
