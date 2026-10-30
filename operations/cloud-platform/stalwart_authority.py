"""One-hour bootstrap authority, seeded and revoked entirely on the platform.

Recovery credentials follow the existing mailbox provisioner's fixed source.
They authenticate only native provisioning requests, never become a runtime key.
"""
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone

from stalwart_admin import canonical, Client
from stalwart_bootstrap import BootstrapClient, PERMISSIONS, broker_call, created, fail, find, load_configuration

NAME = 're8ch-self-service-provisioner'
SECRET = 'stalwart-bootstrap-authority'
NAMESPACE = 'ai-operations'
PERMS = sorted(set(PERMISSIONS + ['impersonate', 'sysAccountCreate', 'sysApiKeyCreate',
             'sysApiKeyGet', 'sysOAuthClientCreate', 'sysOAuthClientGet', 'sysOAuthClientQuery']))


class RecoveryClient(BootstrapClient):
    def authorization_header(self):
        return 'Basic ' + self.token


def secret(service, name):
    value = broker_call(service, ['get', 'secret', name, '-n', NAMESPACE, '--ignore-not-found', '-o', 'json'])
    if value['exitCode']:
        fail('AUTHORITY_SECRET_UNAVAILABLE')
    try:
        return json.loads(value['stdout']) if value['stdout'].strip() else None
    except ValueError:
        fail('AUTHORITY_SECRET_UNAVAILABLE')


def recovery(service, config):
    value = broker_call(service, ['get', 'secret', 'stalwart-recovery-admin', '-n', 'mail-system', '-o', 'json'])
    if value['exitCode']:
        fail('AUTHORITY_RECOVERY_UNAVAILABLE')
    try:
        raw = base64.b64decode(json.loads(value['stdout'])['data']['STALWART_RECOVERY_ADMIN'], validate=True)
        user, colon, password = raw.partition(b':')
        if not colon or not user or not password or len(raw) > 16384 or any(c in raw for c in (b'\r', b'\n', b'\x00')):
            fail('AUTHORITY_RECOVERY_INVALID')
        # Raw recovery bytes remain in this process; no subprocess argument,
        # native method argument or tool result carries them.
        return RecoveryClient(config['origin'], base64.b64encode(raw).decode())
    except (ValueError, KeyError, TypeError):
        fail('AUTHORITY_RECOVERY_INVALID')


def account_value(config):
    return {'@type': 'User', 'name': NAME, 'domainId': config['domainId'],
            'description': 'Temporary Re8ch self-service provisioning authority',
            'roles': {'@type': 'User'}, 'memberGroupIds': {},
            'permissions': {'@type': 'Replace', 'enabledPermissions': {p: True for p in PERMS}, 'disabledPermissions': {}}}


def inspect(config, client):
    metadata = client.request(client.endpoint + '/.well-known/oauth-authorization-server')
    if not {'urn:ietf:params:oauth:scope:' + x for x in ['mail', 'calendars', 'files']} <= set(metadata.get('scopes_supported', [])):
        fail('AUTHORITY_SCOPES_UNAVAILABLE')
    client.discover()
    expected = account_value(config)
    account = find(client, 'Account', 'name', NAME, list(expected))
    if account and any(account.get(k) != v for k, v in expected.items()):
        fail('AUTHORITY_ACCOUNT_CONFLICT')
    return account


def key_metadata(client, account_id, key_id):
    values = client.method('ApiKey', 'get', {'accountId': account_id, 'ids': [key_id],
                 'properties': ['id', 'permissions', 'expiresAt']}).get('list', [])
    if len(values) != 1 or values[0].get('id') != key_id:
        fail('AUTHORITY_KEY_UNAVAILABLE')
    return values[0]


def call(service, args, subject, *, config=None, client=None):
    if set(args) - {'action', 'apply', 'planHash'} or type(args.get('apply', False)) is not bool or args.get('action', 'seed') not in {'seed', 'revoke'}:
        fail('INVALID_ARGUMENT')
    config = config or load_configuration()
    client = client or recovery(service, config)
    account = inspect(config, client)
    existing = secret(service, SECRET)
    action = args.get('action', 'seed')
    meta = existing.get('metadata', {}) if existing else {}
    ann = meta.get('annotations', {})
    if existing and (meta.get('labels', {}).get('re8ch.com/credential-owner') != NAME or ann.get('re8ch.com/provider-origin') != config['origin']):
        fail('AUTHORITY_SECRET_CONFLICT')
    if existing and ann.get('re8ch.com/bootstrap-state') == 'Revoked':
        return {'state': 'Revoked', 'changed': False}
    revoking = ann.get('re8ch.com/bootstrap-state') == 'Revoking'
    if existing and (ann.get('re8ch.com/bootstrap-state') not in {'Ready', 'Revoking'} or not account or ann.get('re8ch.com/provider-account') != account['id']):
        fail('AUTHORITY_RECOVERY_REQUIRED')
    if revoking:
        if action != 'revoke':
            fail('AUTHORITY_RECOVERY_REQUIRED')
        status = client.method('ApiKey', 'get', {'accountId': account['id'], 'ids': [ann.get('re8ch.com/provider-key')], 'properties': ['id']})
        if status.get('list') or status.get('notFound') != [ann.get('re8ch.com/provider-key')]:
            fail('AUTHORITY_REVOKE_UNCERTAIN')
    elif existing:
        key = key_metadata(client, account['id'], ann.get('re8ch.com/provider-key'))
        if key.get('permissions') != {'@type': 'Replace', 'permissions': {p: True for p in PERMS}} or key.get('expiresAt') != ann.get('re8ch.com/expires-at'):
            fail('AUTHORITY_KEY_CONFLICT')
        if action == 'seed':
            try:
                if datetime.fromisoformat(key['expiresAt'].replace('Z', '+00:00')) <= datetime.now(timezone.utc):
                    fail('AUTHORITY_EXPIRED')
            except (KeyError, ValueError):
                fail('AUTHORITY_KEY_CONFLICT')
            try:
                token = base64.b64decode(existing['data']['token'], validate=True).decode()
                Client(config['origin'], token).discover()
            except (KeyError, ValueError, UnicodeError):
                fail('AUTHORITY_SECRET_CONFLICT')
            return {'state': 'Ready', 'changed': False, 'credentialRef': f'kubernetes://{NAMESPACE}/{SECRET}'}
    if action == 'revoke':
        ready = secret(service, 'stalwart-self-service-admin')
        runtime_meta = ready.get('metadata', {}) if ready else {}
        runtime_ann = runtime_meta.get('annotations', {})
        if not existing or runtime_ann.get('re8ch.com/bootstrap-state') != 'Ready' or runtime_ann.get('re8ch.com/provider-origin') != config['origin'] or runtime_meta.get('labels', {}).get('re8ch.com/credential-owner') != 're8ch-self-service-management':
            fail('AUTHORITY_RUNTIME_NOT_READY')
    plan = {'action': action, 'subject': subject, 'revision': config['revision'], 'origin': config['origin'],
            'domainId': config['domainId'], 'accountId': account['id'] if account else None,
            'credentialRef': f'kubernetes://{NAMESPACE}/{SECRET}', 'permissions': PERMS,
            'revoking': revoking, 'secretUID': meta.get('uid'), 'keyId': ann.get('re8ch.com/provider-key'), 'expiresInSeconds': 3600}
    hash_value = hashlib.sha256(canonical(plan)).hexdigest()
    if not args.get('apply'):
        return {'plan': plan, 'planHash': hash_value, 'applied': False}
    if args.get('planHash') != hash_value:
        fail('AUTHORITY_PLAN_MISMATCH')
    if action == 'revoke':
        if not revoking:
            existing['metadata']['annotations']['re8ch.com/bootstrap-state'] = 'Revoking'
            reserved = broker_call(service, ['replace', '-f', '-', '-o', 'name'], stdin=json.dumps(existing))
            if reserved['exitCode']:
                fail('AUTHORITY_REVOKE_UNCERTAIN')
            result = client.method('ApiKey', 'set', {'accountId': account['id'], 'destroy': [plan['keyId']]})
            if result.get('destroyed') != [plan['keyId']]:
                fail('AUTHORITY_REVOKE_UNCERTAIN')
        current = secret(service, SECRET)
        cm = current.get('metadata', {}) if current else {}
        ca = cm.get('annotations', {})
        if cm.get('uid') != plan['secretUID'] or cm.get('labels', {}).get('re8ch.com/credential-owner') != NAME or ca.get('re8ch.com/provider-origin') != config['origin'] or ca.get('re8ch.com/provider-key') != plan['keyId'] or ca.get('re8ch.com/bootstrap-state') != 'Revoking':
            fail('AUTHORITY_REVOKE_UNCERTAIN')
        current['data'] = {}
        current['metadata']['annotations']['re8ch.com/bootstrap-state'] = 'Revoked'
        result = broker_call(service, ['replace', '-f', '-', '-o', 'name'], stdin=json.dumps(current))
        if result['exitCode']:
            fail('AUTHORITY_REVOKE_UNCERTAIN')
        observed = secret(service, SECRET)
        if not observed or observed.get('data') or observed.get('metadata', {}).get('annotations', {}).get('re8ch.com/bootstrap-state') != 'Revoked':
            fail('AUTHORITY_REVOKE_UNCERTAIN')
        return {'state': 'Revoked', 'changed': True, 'credentialRef': plan['credentialRef']}
    manifest = {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
                'metadata': {'name': SECRET, 'namespace': NAMESPACE,
                  'labels': {'re8ch.com/credential-owner': NAME},
                  'annotations': {'re8ch.com/bootstrap-state': 'Provisioning', 're8ch.com/plan-hash': hash_value,
                                  're8ch.com/provider-origin': config['origin']}}}
    result = broker_call(service, ['create', '-f', '-', '-o', 'name'], stdin=json.dumps(manifest))
    if result['exitCode']:
        fail('AUTHORITY_RESERVATION_FAILED')
    reservation = secret(service, SECRET)
    if not reservation or not reservation.get('metadata', {}).get('uid'):
        fail('AUTHORITY_RESERVATION_LOST')
    uid = reservation['metadata']['uid']
    if not account:
        account = created(client, 'Account', account_value(config))
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    key = created(client, 'ApiKey', {'description': NAME, 'expiresAt': expiry,
                  'permissions': {'@type': 'Replace', 'permissions': {p: True for p in PERMS}}}, account['id'])
    actual = key_metadata(client, account['id'], key['id'])
    if actual.get('permissions') != {'@type': 'Replace', 'permissions': {p: True for p in PERMS}} or actual.get('expiresAt') != expiry:
        fail('AUTHORITY_KEY_CONFLICT')
    token = key.get('secret')
    if not isinstance(token, str) or not token:
        fail('AUTHORITY_KEY_UNAVAILABLE')
    Client(config['origin'], token).discover()
    current = secret(service, SECRET)
    m = current.get('metadata', {}) if current else {}
    a = m.get('annotations', {})
    if m.get('uid') != uid or m.get('labels', {}).get('re8ch.com/credential-owner') != NAME or a.get('re8ch.com/plan-hash') != hash_value or a.get('re8ch.com/bootstrap-state') != 'Provisioning':
        fail('AUTHORITY_RESERVATION_LOST')
    manifest['metadata']['resourceVersion'] = m['resourceVersion']
    manifest['metadata']['annotations'].update({'re8ch.com/bootstrap-state': 'Ready', 're8ch.com/provider-account': account['id'],
             're8ch.com/provider-key': key['id'], 're8ch.com/expires-at': expiry})
    manifest['data'] = {'token': base64.b64encode(token.encode()).decode()}
    result = broker_call(service, ['replace', '-f', '-', '-o', 'name'], stdin=json.dumps(manifest))
    if result['exitCode']:
        fail('AUTHORITY_SAVE_UNCERTAIN')
    observed = secret(service, SECRET)
    if not observed or observed.get('data') != manifest['data']:
        fail('AUTHORITY_SAVE_UNCERTAIN')
    return {'state': 'Ready', 'changed': True, 'credentialRef': plan['credentialRef'], 'expiresAt': expiry,
            'operationId': result['operationId'], 'auditRef': result['auditRef']}
