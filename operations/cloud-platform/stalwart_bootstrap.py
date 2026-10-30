"""Fixed, server-side Stalwart v1 bootstrap; credential bytes never cross MCP.

Deployment supplies a reviewed config and bootstrap bearer file. This operation
creates a separate restricted account/key, not a copy of the bootstrap credential.
"""
import base64
import hashlib
import json
import os
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone

from runtime import OperationError
from stalwart_admin import Client, canonical, CAPABILITY

NAME = 're8ch-self-service-management'
CLIENT_ID = 're8ch-self-service'
SECRET = 'stalwart-self-service-admin'
NAMESPACE = 'ai-operations'
PERMISSIONS = ['authenticate', 'sysAccountGet', 'sysAccountQuery', 'sysAccountUpdate',
               'sysDomainGet', 'sysDomainQuery', 'sysDomainUpdate']
CLIENT = {'clientId': CLIENT_ID, 'description': 'Re8ch self-service user-authorized mail, calendars and lightweight files',
          'firstParty': False, 'scopes': {'mail': True, 'calendars': True, 'files': True},
          'grantTypes': {'deviceCode': True}, 'redirectUris': {}}


def fail(code):
    raise OperationError(code, 'Stalwart bootstrap failed closed; inspect restricted server-side evidence')


class BootstrapClient(Client):
    def method(self, obj, action, args):
        if obj not in {'Account', 'OAuthClient', 'ApiKey', 'Domain'} or action not in {'query', 'get', 'set'} or (obj == 'Domain' and action == 'set'):
            fail('BOOTSTRAP_METHOD_DENIED')
        if self.api_url is None:
            self.discover()
        name = 'x:' + obj + '/' + action
        result = self.request(self.api_url, {'using': ['urn:ietf:params:jmap:core', CAPABILITY],
                             'methodCalls': [[name, args, 'bootstrap']]})
        rows = result.get('methodResponses', [])
        if len(rows) != 1 or len(rows[0]) != 3 or rows[0][0] != name or rows[0][2] != 'bootstrap' or not isinstance(rows[0][1], dict):
            fail('BOOTSTRAP_PROVIDER_REJECTED')
        value = rows[0][1]
        if any(value.get(k) for k in ['notCreated', 'notUpdated', 'notDestroyed']):
            fail('BOOTSTRAP_PROVIDER_REJECTED')
        return value


def load_configuration(*, allow_missing_domain=False):
    try:
        config = json.loads(Path(os.environ['STALWART_BOOTSTRAP_CONFIG']).read_text())
        if set(config) != {'origin', 'domainId', 'revision', 'tokenFile'} or not all(isinstance(v, str) and (v or (k == 'domainId' and allow_missing_domain)) for k, v in config.items()):
            fail('BOOTSTRAP_CONFIGURATION_INVALID')
        if not re.fullmatch(r'[0-9a-f]{40,64}', config['revision']) or config['tokenFile'] != '/etc/stalwart-bootstrap/credential/token':
            fail('BOOTSTRAP_CONFIGURATION_INVALID')
        return config
    except (KeyError, OSError, ValueError):
        fail('BOOTSTRAP_CONFIGURATION_INVALID')


def configured():
    config = load_configuration()
    try:
        with open(config['tokenFile'], 'rb') as handle:
            token = handle.read(16385).decode().strip()
        client = BootstrapClient(config['origin'], token)
    except (OSError, ValueError):
        fail('BOOTSTRAP_CONFIGURATION_INVALID')
    return config, client


def find(client, obj, field, value, properties):
    found = client.method(obj, 'query', {'filter': {field: value}, 'limit': 2})
    ids = found.get('ids', [])
    if not isinstance(ids, list) or len(ids) > 1 or found.get('total', len(ids)) > 1:
        fail('BOOTSTRAP_OBJECT_CONFLICT')
    if not ids:
        return None
    rows = client.method(obj, 'get', {'ids': ids, 'properties': ['id', *properties]}).get('list', [])
    if len(rows) != 1 or rows[0].get('id') != ids[0] or rows[0].get(field) != value:
        fail('BOOTSTRAP_OBJECT_CONFLICT')
    return rows[0]


def account_value(config):
    return {'@type': 'User', 'name': NAME, 'domainId': config['domainId'],
            'description': 'Dedicated Re8ch bounded management identity',
            'roles': {'@type': 'User'}, 'memberGroupIds': {},
            'permissions': {'@type': 'Replace', 'enabledPermissions': {p: True for p in PERMISSIONS}, 'disabledPermissions': {}}}


def inspect(config, client):
    # Capability check happens before any mutation, never fallback to v0.16 APIs.
    metadata = client.request(client.endpoint + '/.well-known/oauth-authorization-server')
    required = {'urn:ietf:params:oauth:scope:' + name for name in ['mail', 'calendars', 'files']}
    if not required <= set(metadata.get('scopes_supported', [])):
        fail('BOOTSTRAP_SCOPES_UNAVAILABLE')
    client.discover()
    expected = account_value(config)
    account = find(client, 'Account', 'name', NAME, list(expected))
    if account and any(account.get(k) != v for k, v in expected.items()):
        fail('BOOTSTRAP_ACCOUNT_CONFLICT')
    oauth = find(client, 'OAuthClient', 'clientId', CLIENT_ID, list(CLIENT))
    if oauth and any(oauth.get(k) != v for k, v in CLIENT.items()):
        fail('BOOTSTRAP_CLIENT_CONFLICT')
    return account, oauth


def created(client, obj, value, account_id=None):
    args = {'create': {'re8ch': value}}
    if account_id:
        args['accountId'] = account_id
    result = client.method(obj, 'set', args).get('created', {}).get('re8ch', {})
    if not isinstance(result.get('id'), str) or not result['id']:
        fail('BOOTSTRAP_CREATE_UNCERTAIN')
    return result


def broker_call(service, argv, stdin=None):
    try:
        return service._kubectl(argv, stdin=stdin)
    except OperationError:
        fail('BOOTSTRAP_BROKER_UNCERTAIN')


def secret_get(service):
    result = broker_call(service, ['get', 'secret', SECRET, '-n', NAMESPACE, '--ignore-not-found', '-o', 'json'])
    if result['exitCode']:
        fail('BOOTSTRAP_SECRET_READ_FAILED')
    try:
        return json.loads(result['stdout']) if result['stdout'].strip() else None
    except ValueError:
        fail('BOOTSTRAP_SECRET_READ_FAILED')


def call(service, args, subject, *, configuration=None, client=None):
    if set(args) - {'apply', 'planHash'} or type(args.get('apply', False)) is not bool:
        fail('INVALID_ARGUMENT')
    if configuration is None:
        configuration, client = configured()
    account, oauth = inspect(configuration, client)
    existing = secret_get(service)
    if existing:
        labels = existing.get('metadata', {}).get('labels', {})
        annotations = existing.get('metadata', {}).get('annotations', {})
        if labels.get('re8ch.com/credential-owner') != NAME or not account or not oauth:
            fail('BOOTSTRAP_SECRET_CONFLICT')
        if annotations.get('re8ch.com/bootstrap-state') != 'Ready' or not existing.get('data', {}).get('token'):
            fail('BOOTSTRAP_RECOVERY_REQUIRED')
        if annotations.get('re8ch.com/provider-account') != account['id'] or annotations.get('re8ch.com/provider-origin') != configuration['origin']:
            fail('BOOTSTRAP_SECRET_CONFLICT')
        try:
            expires = datetime.fromisoformat(annotations['re8ch.com/expires-at'].replace('Z', '+00:00'))
            if expires <= datetime.now(timezone.utc):
                fail('BOOTSTRAP_CREDENTIAL_EXPIRED')
            existing_token = base64.b64decode(existing['data']['token'], validate=True).decode()
            Client(configuration['origin'], existing_token).discover()
        except (KeyError, ValueError, UnicodeError):
            fail('BOOTSTRAP_SECRET_CONFLICT')
        keys = client.method('ApiKey', 'get', {'accountId': account['id'],
                'ids': [annotations.get('re8ch.com/provider-key')],
                'properties': ['id', 'permissions', 'expiresAt']}).get('list', [])
        if len(keys) != 1 or keys[0].get('id') != annotations.get('re8ch.com/provider-key') or keys[0].get('expiresAt') != annotations['re8ch.com/expires-at'] or keys[0].get('permissions') != {'@type': 'Replace', 'permissions': {p: True for p in PERMISSIONS}}:
            fail('BOOTSTRAP_KEY_VERIFY_FAILED')
        return {'state': 'Ready', 'credentialRef': f'kubernetes://{NAMESPACE}/{SECRET}', 'changed': False}
    plan = {'revision': configuration['revision'], 'origin': configuration['origin'],
            'domainId': configuration['domainId'], 'subject': subject,
            'accountId': account['id'] if account else None, 'clientId': CLIENT_ID,
            'clientExists': bool(oauth), 'permissions': PERMISSIONS, 'scopes': list(CLIENT['scopes']),
            'credentialRef': f'kubernetes://{NAMESPACE}/{SECRET}', 'expiresInDays': 30}
    plan_hash = hashlib.sha256(canonical(plan)).hexdigest()
    if not args.get('apply'):
        return {'plan': plan, 'planHash': plan_hash, 'applied': False}
    if args.get('planHash') != plan_hash:
        fail('BOOTSTRAP_PLAN_MISMATCH')
    # Reserve the fixed Secret before native mutation. Concurrent calls cannot
    # generate multiple keys. Any interrupted attempt requires operator recovery.
    manifest = {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
                'metadata': {'name': SECRET, 'namespace': NAMESPACE,
                 'labels': {'re8ch.com/credential-owner': NAME},
                 'annotations': {'re8ch.com/bootstrap-state': 'Provisioning', 're8ch.com/plan-hash': plan_hash}}}
    reserved = broker_call(service, ['create', '-f', '-', '-o', 'name'], stdin=json.dumps(manifest))
    if reserved['exitCode']:
        fail('BOOTSTRAP_RESERVATION_FAILED')
    reservation = secret_get(service)
    if not reservation or not reservation.get('metadata', {}).get('uid'):
        fail('BOOTSTRAP_RESERVATION_LOST')
    reservation_uid = reservation['metadata']['uid']
    if not oauth:
        created(client, 'OAuthClient', CLIENT)
    if not account:
        account = created(client, 'Account', account_value(configuration))
    expiry = (datetime.now(timezone.utc) + timedelta(days=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
    key = created(client, 'ApiKey', {'description': NAME, 'expiresAt': expiry,
                  'permissions': {'@type': 'Replace', 'permissions': {p: True for p in PERMISSIONS}}}, account['id'])
    actual = client.method('ApiKey', 'get', {'accountId': account['id'], 'ids': [key['id']],
                  'properties': ['id', 'description', 'permissions', 'expiresAt']}).get('list', [])
    expected_permissions = {'@type': 'Replace', 'permissions': {p: True for p in PERMISSIONS}}
    if len(actual) != 1 or actual[0].get('id') != key['id'] or actual[0].get('permissions') != expected_permissions or actual[0].get('expiresAt') != expiry:
        fail('BOOTSTRAP_KEY_VERIFY_FAILED')
    token = key.get('secret')
    if not isinstance(token, str) or not token or len(token) > 16384 or any(c.isspace() for c in token):
        fail('BOOTSTRAP_KEY_UNAVAILABLE')
    # Validate the generated credential before exposing it to the runtime.
    restricted = Client(configuration['origin'], token)
    restricted.discover()
    current = secret_get(service)
    metadata = current.get('metadata', {}) if current else {}
    annotations = metadata.get('annotations', {})
    if metadata.get('uid') != reservation_uid or annotations.get('re8ch.com/plan-hash') != plan_hash or annotations.get('re8ch.com/bootstrap-state') != 'Provisioning' or metadata.get('labels', {}).get('re8ch.com/credential-owner') != NAME:
        fail('BOOTSTRAP_RESERVATION_LOST')
    manifest['metadata']['resourceVersion'] = current['metadata']['resourceVersion']
    manifest['metadata']['annotations'].update({'re8ch.com/bootstrap-state': 'Ready',
            're8ch.com/provider-account': account['id'], 're8ch.com/provider-origin': configuration['origin'], 're8ch.com/provider-key': key['id'], 're8ch.com/expires-at': expiry})
    manifest['data'] = {'token': base64.b64encode(token.encode()).decode()}
    saved = broker_call(service, ['replace', '-f', '-', '-o', 'name'], stdin=json.dumps(manifest))
    if saved['exitCode']:
        fail('BOOTSTRAP_SECRET_WRITE_FAILED')
    observed = secret_get(service)
    if not observed or observed.get('data') != manifest['data']:
        fail('BOOTSTRAP_SECRET_VERIFY_FAILED')
    return {'state': 'Ready', 'credentialRef': plan['credentialRef'], 'changed': True,
            'expiresAt': expiry, 'operationId': saved['operationId'], 'auditRef': saved['auditRef']}
