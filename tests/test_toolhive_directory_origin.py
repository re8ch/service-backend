from pathlib import Path
import yaml

ROOT = Path(__file__).parents[1]


def test_directory_public_origin_matches_routes_and_oauth_callback():
    documents = list(yaml.safe_load_all((ROOT / 'clusters/re8ch/components/toolhive/directory.yaml').read_text()))
    deployment = next(doc for doc in documents if doc.get('kind') == 'Deployment' and doc['metadata']['name'] == 'toolhive-cloud-ui')
    env_items = deployment['spec']['template']['spec']['containers'][0]['env']
    env = {item['name']: item.get('value') for item in env_items}
    host = 'tools.re8ch.com'
    origin = 'https://' + host
    assert env['OIDC_ISSUER_URL'] == 'https://db-rw.re8ch.com/auth/v1'
    assert env['OIDC_CLIENT_ID'] == '32098a11-be0c-5dd4-9f29-ec87c30faf19'
    assert 'OIDC_CLIENT_SECRET' not in env
    assert env['BETTER_AUTH_URL'] == origin
    assert env['TRUSTED_ORIGINS'].split(',') == [origin]
    assert any(doc.get('kind') == 'HTTPRoute' and host in doc['spec'].get('hostnames', []) for doc in documents)
    oauth = (ROOT / 'operations/supabase-identity/oauth-clients-toolhive.yaml').read_text()
    assert oauth.count(origin + '/api/auth/oauth2/callback/oidc') == 2
    assert "'public'" in oauth and "'none'" in oauth
    assert env['OIDC_CLIENT_ID'] in oauth

def test_toolhive_directory_is_colocated_on_r640():
    source = ROOT / "clusters/re8ch/components/toolhive/directory.yaml"
    deployments = [doc for doc in yaml.safe_load_all(source.read_text()) if doc and doc.get("kind") == "Deployment"]
    assert [item["metadata"]["name"] for item in deployments] == ["toolhive-cloud-ui"]
    deployment = deployments[0]
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["template"]["spec"]["nodeSelector"] == {"kubernetes.io/hostname": "r640"}
