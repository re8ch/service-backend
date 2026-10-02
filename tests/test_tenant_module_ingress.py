from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]

def test_platform_modules_only_admit_gateway_from_tenant_namespace():
    docs = list(yaml.safe_load_all((ROOT / 'clusters/re8ch/components/toolhive/vmcp-external-publication.yaml').read_text()))
    policy = next(d for d in docs if d and d['kind'] == 'NetworkPolicy' and d['metadata']['name'] == 'vmcp-internal-backends')
    assert policy['spec']['ingress'] == [{
        'from': [
            {'podSelector': {'matchLabels': {'app': 'mcpserver', 'toolhive': 'true'}}},
            {'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'tenant-infrastructure'}},
             'podSelector': {'matchLabels': {'app': 'tenant-api'}}},
        ],
        'ports': [{'protocol': 'TCP', 'port': 8088}],
    }]
    matches = policy['spec']['podSelector']['matchExpressions']
    assert next(m for m in matches if m['key'] == 'app')['values'] == [
        'ops-admin-vmcp-internal', 'ops-observe-vmcp-internal', 'ops-operate-vmcp-internal'
    ]
