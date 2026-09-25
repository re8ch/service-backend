"""Guard the fast reconciliation entrypoint and stateless controller failover."""
import plistlib
import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_root_frequency_and_local_sync():
    docs = list(yaml.safe_load_all((ROOT / 'clusters/re8ch/flux-system/gotk-sync.yaml').read_text()))
    assert docs[0]['spec']['interval'] == '30s'
    assert docs[1]['spec']['interval'] == '1m'
    assert docs[0]['spec']['timeout'] == '15m'
    assert docs[1]['spec']['timeout'] == '3m'
    assert docs[0]['spec']['proxySecretRef']['name'] == 'service-backend-control-plane-proxy'
    proxy = yaml.safe_load((ROOT / 'clusters/re8ch/flux-system/service-backend-control-plane-proxy.yaml').read_text())
    assert proxy['stringData']['address'] == (
        'http://identity-egress-control-plane.egress-fabric.svc.cluster.local:17893')
    config = plistlib.loads((ROOT / 'operations/workspace-controller/macos/com.re8ch.service-backend-anchor-sync.plist').read_bytes())
    assert config['StartInterval'] == 30


def test_rendered_controller_failover_and_source_cache_safety():
    command = ['kustomize', 'build'] if shutil.which('kustomize') else ['kubectl', 'kustomize']
    rendered = subprocess.check_output([*command, str(ROOT / 'clusters/re8ch')], text=True)
    docs = list(yaml.safe_load_all(rendered))
    services = {d['metadata']['name']: d for d in docs if d and d['kind'] == 'Service'}
    deployments = {d['metadata']['name']: d for d in docs if d and d['kind'] == 'Deployment' and d['metadata'].get('namespace') == 'flux-system'}
    for name in ('helm-controller', 'kustomize-controller', 'notification-controller'):
        deployment = deployments[name]
        assert deployment['spec']['replicas'] == 3
        pod = deployment['spec']['template']['spec']
        assert '--enable-leader-election' in pod['containers'][0]['args']
        anti_affinity = pod['affinity']['podAntiAffinity']['requiredDuringSchedulingIgnoredDuringExecution'][0]
        assert anti_affinity['topologyKey'] == 'kubernetes.io/hostname'
        assert anti_affinity['matchLabelKeys'] == ['app']
    # Never load-balance artifact requests over independently empty caches.
    source = deployments['source-controller']
    assert source['spec']['replicas'] == 1
    assert source['spec']['template']['spec']['dnsPolicy'] == 'ClusterFirst'
    expression = source['spec']['template']['spec']['affinity']['nodeAffinity'][
        'requiredDuringSchedulingIgnoredDuringExecution'
    ]['nodeSelectorTerms'][0]['matchExpressions'][0]
    assert expression == {
        'key': 'kubernetes.io/hostname',
        'operator': 'In',
        'values': ['restricted-node-6dc3f140f9'],
    }
    assert services['identity-egress-control-plane']['spec']['internalTrafficPolicy'] == 'Local'
