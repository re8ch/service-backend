"""Guard the fast reconciliation entrypoint and stateless controller failover."""
import plistlib
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


def test_retired_controllers_do_not_return_and_unrelated_egress_is_preserved(kustomize_documents):
    docs = kustomize_documents("clusters/re8ch")
    docs += kustomize_documents("clusters/re8ch/domains/networking")
    services = {d['metadata']['name']: d for d in docs if d and d['kind'] == 'Service'}
    deployments = {d['metadata']['name'] for d in docs if d and d['kind'] == 'Deployment' and d['metadata'].get('namespace') == 'flux-system'}
    assert not deployments & {'helm-controller', 'notification-controller', 'kustomize-controller', 'source-controller'}
    assert services['identity-egress-control-plane']['spec']['internalTrafficPolicy'] == 'Local'
