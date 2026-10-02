from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]

def test_session_migration_scripts_are_mounted_and_use_separate_owners():
    config, job = list(yaml.safe_load_all((ROOT / 'operations/supabase-identity/platform-admin-api.yaml').read_text()))
    assert set(config['metadata']) == {'name', 'namespace'}
    pod = job['spec']['template']['spec']
    volume = next(v for v in pod['volumes'] if v['name'] == 'sql')
    assert volume['configMap']['name'] == config['metadata']['name']
    containers = pod['initContainers'] + pod['containers']
    for container in containers:
        scripts = re.findall(r'/sql/([\w.-]+)', ' '.join(container['args']))
        assert scripts
        assert all(script in config['data'] for script in scripts)
        assert {'name': 'sql', 'mountPath': '/sql', 'readOnly': True} in container['volumeMounts']
    auth_env = {v['name']: v for v in pod['initContainers'][0]['env']}
    assert auth_env['GOTRUE_DB_DATABASE_URL']['valueFrom']['secretKeyRef']['key'] == 'GOTRUE_DB_DATABASE_URL'
    owner_env = {v['name']: v for v in pod['containers'][0]['env']}
    assert owner_env['PGUSER']['valueFrom']['secretKeyRef']['key'] == 'POSTGRES_USER_READ_WRITE'
    assert 'references(id)' in config['data']['auth-owner.sql']
    assert 'grant select *' not in config['data']['auth-owner.sql']

    auth_sql = config['data']['auth-owner.sql']
    assert 'function auth.platform_session_active()' in auth_sql
    assert 'owner to supabase_auth_admin' in auth_sql
    assert 'from public, anon, authenticated' in auth_sql
    assert 'grant execute on function auth.platform_session_active() to supabase_admin' in auth_sql
    migration = config['data']['migration.sql']
    assert 'auth.platform_session_active()' in migration
    assert 'from auth.sessions' not in migration
    assert 'from auth.refresh_tokens' not in migration


def test_platform_migration_reloads_the_read_only_schema_cache():
    _, job = list(yaml.safe_load_all((ROOT / 'operations/supabase-identity/platform-admin-api.yaml').read_text()))
    _, deployment = list(yaml.safe_load_all((ROOT / 'operations/supabase-identity/identity-rest-ro.yaml').read_text()))
    # RO disables LISTEN, so the same migration revision must roll its process.
    assert deployment['spec']['template']['metadata']['annotations']['identity.re8ch.com/schema-revision'] == job['metadata']['name']
