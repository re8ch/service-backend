from pathlib import Path
import yaml

ROOT = Path(__file__).parents[1]


def test_directory_public_origin_matches_routes_and_oauth_callback():
    documents = list(yaml.safe_load_all((ROOT / 'clusters/re8ch/components/toolhive/external-observe-publication.yaml').read_text()))
    deployment = next(doc for doc in documents if doc.get('kind') == 'Deployment' and doc['metadata']['name'] == 'toolhive-cloud-ui')
    env = {item['name']: item.get('value') for item in deployment['spec']['template']['spec']['containers'][0]['env']}
    host = 'tools.re8ch.com'
    origin = 'https://' + host
    assert env['BETTER_AUTH_URL'] == origin
    assert env['TRUSTED_ORIGINS'].split(',') == [origin]
    assert any(doc.get('kind') == 'HTTPRoute' and host in doc['spec'].get('hostnames', []) for doc in documents)
    bootstrap = (ROOT / 'clusters/re8ch/components/auth/authentik/bootstrap-toolhive-cloud-ui-oauth.sh').read_text()
    assert bootstrap.count(origin + '/api/auth/oauth2/callback/oidc') == 2
    assert 'https://tools.service.re8ch.com' not in bootstrap


def test_toolhive_stateless_frontends_are_colocated_on_r640():
    sources = (
        ROOT / 'clusters/re8ch/components/toolhive/external-observe-publication.yaml',
        ROOT / 'clusters/re8ch/components/plugin-runtime/runtime.yaml',
    )
    deployments = [
        doc
        for source in sources
        for doc in yaml.safe_load_all(source.read_text())
        if doc and doc.get('kind') == 'Deployment'
    ]
    for name in ('toolhive-cloud-ui', 're8ch-plugin-runtime'):
        deployment = next(item for item in deployments if item['metadata']['name'] == name)
        assert deployment['spec']['replicas'] == 1
        pod_spec = deployment['spec']['template']['spec']
        assert pod_spec['nodeSelector'] == {'kubernetes.io/hostname': 'r640'}
        assert 'podAntiAffinity' not in pod_spec.get('affinity', {})
