"""Guard the fast reconciliation entrypoint and stateless controller failover."""
import plistlib
import shutil
import subprocess
from pathlib import Path

import yaml

# LibYAML keeps SafeLoader constructors; pure Python remains a supported fallback.
YAML_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

ROOT = Path(__file__).resolve().parents[1]


def test_root_frequency_and_local_sync():
    docs = list(yaml.load_all((ROOT / 'clusters/re8ch/flux-system/gotk-sync.yaml').read_text(), Loader=YAML_SAFE_LOADER))
    assert docs[0]['spec']['interval'] == '30s'
    assert docs[1]['spec']['interval'] == '1m'
    assert docs[0]['spec']['timeout'] == '15m'
    assert docs[1]['spec']['timeout'] == '20m'
    assert docs[0]['spec']['url'] == 'ssh://git@ssh.github.com:443/re8ch/service-backend.git'
    assert docs[0]['spec']['secretRef']['name'] == 'service-backend-github-ssh'
    assert docs[0]['spec']['proxySecretRef']['name'] == 'service-backend-github-socks'
    proxy = yaml.load(
        (ROOT / 'clusters/re8ch/flux-system/service-backend-github-socks.yaml').read_text(), Loader=YAML_SAFE_LOADER
    )
    assert proxy['stringData']['address'] == (
        'socks5://mihomos-node-cn-local.egress-fabric.svc.cluster.local:17891'
    )
    assert docs[0]['spec']['verify']['mode'] == 'HEAD'
    secret = yaml.load((ROOT / 'clusters/re8ch/flux-system/service-backend-github-ssh.sops.yaml').read_text(), Loader=YAML_SAFE_LOADER)
    assert secret['stringData']['identity'].startswith('ENC[')
    assert secret['stringData']['known_hosts'].startswith('ENC[')
    config = plistlib.loads((ROOT / 'operations/workspace-controller/macos/com.re8ch.service-backend-anchor-sync.plist').read_bytes())
    assert config['StartInterval'] == 30


def test_rendered_controller_failover_and_source_cache_safety():
    command = ['kustomize', 'build'] if shutil.which('kustomize') else ['kubectl', 'kustomize']
    rendered = subprocess.check_output([*command, str(ROOT / 'clusters/re8ch')], text=True)
    rendered += "\n---\n" + subprocess.check_output(
        [*command, str(ROOT / 'clusters/re8ch/domains/networking')], text=True
    )
    docs = list(yaml.load_all(rendered, Loader=YAML_SAFE_LOADER))
    services = {d['metadata']['name']: d for d in docs if d and d['kind'] == 'Service'}
    deployments = {d['metadata']['name']: d for d in docs if d and d['kind'] == 'Deployment' and d['metadata'].get('namespace') == 'flux-system'}
    for name in ('helm-controller', 'notification-controller'):
        deployment = deployments[name]
        assert deployment['spec']['replicas'] == 2
        pod = deployment['spec']['template']['spec']
        assert '--enable-leader-election' in pod['containers'][0]['args']
        expression = pod['affinity']['nodeAffinity'][
            'requiredDuringSchedulingIgnoredDuringExecution'
        ]['nodeSelectorTerms'][0]['matchExpressions'][0]
        assert expression['values'] == ['overseas-kl', 'restricted-node-6dc3f140f9']
    kustomize = deployments['kustomize-controller']
    assert kustomize['spec']['replicas'] == 1
    expression = kustomize['spec']['template']['spec']['affinity']['nodeAffinity'][
        'requiredDuringSchedulingIgnoredDuringExecution'
    ]['nodeSelectorTerms'][0]['matchExpressions'][0]
    assert expression['values'] == ['restricted-node-296f36f2fb']
    kustomize_env = {
        item['name']: item.get('value')
        for item in kustomize['spec']['template']['spec']['containers'][0]['env']
    }
    assert kustomize_env['KUBERNETES_SERVICE_HOST'] == '192.0.2.242'
    assert '--concurrent=4' in kustomize['spec']['template']['spec']['containers'][0]['args']
    assert 'podAntiAffinity' not in kustomize['spec']['template']['spec']['affinity']
    # Source artifacts use pod-local storage and a non-leader replica does not
    # become ready, so scheduler failover is safer than an empty standby cache.
    source = deployments['source-controller']
    assert source['spec']['replicas'] == 1
    assert source['spec']['strategy']['type'] == 'Recreate'
    assert source['spec']['template']['spec']['hostNetwork'] is False
    assert source['spec']['template']['spec']['dnsPolicy'] == 'ClusterFirst'
    assert source['spec']['template']['spec']['nodeSelector'] == {
        'kubernetes.io/os': 'linux',
    }
    manager = source['spec']['template']['spec']['containers'][0]
    assert all(e['name'] != 'ALL_PROXY' for e in manager['env'])
    expression = source['spec']['template']['spec']['affinity']['nodeAffinity'][
        'requiredDuringSchedulingIgnoredDuringExecution'
    ]['nodeSelectorTerms'][0]['matchExpressions'][0]
    assert expression == {
        'key': 'kubernetes.io/hostname',
        'operator': 'In',
        'values': ['restricted-node-1eb0570f74', 'restricted-node-6dc3f140f9'],
    }
    source_env = {item['name']: item.get('value') for item in manager['env']}
    assert source_env['KUBERNETES_SERVICE_HOST'] == '192.0.2.242'
    assert '--concurrent=4' in manager['args']
    assert services['identity-egress-control-plane']['spec']['internalTrafficPolicy'] == 'Local'
