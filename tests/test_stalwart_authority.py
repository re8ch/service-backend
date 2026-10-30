import base64
import copy
import json
from datetime import datetime, timezone
import pytest
import stalwart_authority as a
from runtime import Identity, OperationError
from test_stalwart_bootstrap import CONFIG, Provider

class Broker:
    def __init__(self):
        self.secrets = {}; self.argv = []; self.fail_save = False
    def _kubectl(self, argv, stdin=None):
        self.argv.append(argv)
        result = {'exitCode': 0, 'stdout': '', 'operationId': 'op', 'auditRef': 'audit://op'}
        if argv[0] == 'get':
            if argv[2] == 'stalwart-recovery-admin':
                result['stdout'] = json.dumps({'data': {'STALWART_RECOVERY_ADMIN': base64.b64encode(b'admin:recovery-only').decode()}})
            else: result['stdout'] = json.dumps(self.secrets[argv[2]]) if argv[2] in self.secrets else ''
        else:
            value = json.loads(stdin); name = value['metadata']['name']
            if argv[0] == 'create' and name in self.secrets: result['exitCode'] = 1
            elif argv[0] == 'replace' and self.fail_save: result.update(exitCode=1, stderr=stdin)
            else:
                value['metadata'].update(uid='fixed-uid', resourceVersion='1')
                self.secrets[name] = value
        return result

class Native(Provider):
    def __init__(self):
        super().__init__()
        self.objects['Domain'] = {'id': CONFIG['domainId'], 'name': 'example.test', 'enable': True}
    def method(self, obj, action, args):
        if obj == 'ApiKey' and action == 'get' and obj not in self.objects:
            return {'list': [], 'notFound': args['ids']}
        if action == 'set' and 'destroy' in args:
            self.writes.append((obj, args)); del self.objects['ApiKey']
            return {'destroyed': args['destroy']}
        return super().method(obj, action, args)

def invoke(broker, native, args):
    return a.call(broker, args, 'platform-admin', config=CONFIG, client=native)

def test_seed_uses_fixed_server_side_recovery_source_without_argv_or_result_leak():
    broker = Broker()
    client = a.recovery(broker, CONFIG)
    assert client.authorization_header() == 'Basic ' + base64.b64encode(b'admin:recovery-only').decode()
    assert 'recovery-only' not in json.dumps(broker.argv)
    assert broker.argv == [['get', 'secret', 'stalwart-recovery-admin', '-n', 'mail-system', '-o', 'json']]
    from stalwart_admin import NoRedirect
    assert any(isinstance(handler, NoRedirect) for handler in client.opener.handlers)

def test_seed_generates_one_hour_separate_key_and_never_replays(monkeypatch):
    monkeypatch.setattr(a.Client, 'discover', lambda self: None)
    broker, native = Broker(), Native()
    plan = invoke(broker, native, {})
    assert not native.writes and not broker.secrets
    result = invoke(broker, native, {'apply': True, 'planHash': plan['planHash']})
    assert result['state'] == 'Ready'
    assert 'private-generated-key' not in json.dumps(result)
    stored = broker.secrets[a.SECRET]
    assert stored['data']['token'] == base64.b64encode(b'private-generated-key').decode()
    delta = datetime.fromisoformat(result['expiresAt'].replace('Z', '+00:00')) - datetime.now(timezone.utc)
    assert 3590 < delta.total_seconds() <= 3600
    assert set(native.objects['ApiKey']['permissions']['permissions']) == set(a.PERMS)
    assert 'superuser' not in a.PERMS and 'sysApiKeyDestroy' not in a.PERMS
    assert set(a.PERMISSIONS) <= set(a.PERMS)
    assert invoke(broker, native, {})['changed'] is False
    assert len(native.writes) == 2

@pytest.mark.parametrize('reason', ['scope', 'plan', 'foreign-secret', 'account'])
def test_seed_preconditions_reject_without_writes(reason):
    broker, native = Broker(), Native()
    if reason == 'scope': native.scopes.remove('files')
    if reason == 'account': native.objects['Account'] = {'id': 'foreign', 'name': a.NAME}
    if reason == 'foreign-secret': broker.secrets[a.SECRET] = {'metadata': {}}
    with pytest.raises(OperationError): invoke(broker, native, {'apply': True, 'planHash': 'bad'})
    assert not native.writes

def test_uncertain_save_preserves_reservation_and_never_exposes_or_reissues_key(monkeypatch):
    monkeypatch.setattr(a.Client, 'discover', lambda self: None)
    broker, native = Broker(), Native(); broker.fail_save = True
    plan = invoke(broker, native, {})
    with pytest.raises(OperationError) as error: invoke(broker, native, {'apply': True, 'planHash': plan['planHash']})
    assert 'private-generated-key' not in str(error.value)
    with pytest.raises(OperationError) as error: invoke(broker, native, {})
    assert error.value.code == 'AUTHORITY_RECOVERY_REQUIRED'
    assert len(native.writes) == 2

def test_authority_tool_is_not_visible_or_callable_by_member():
    import server
    member = Identity('member', ())
    assert 'credentials.stalwart.authority.ensure' not in {x['name'] for x in server.tool_list(member)}
    with pytest.raises(OperationError): server.dispatch(None, 'credentials.stalwart.authority.ensure', {}, member)

def test_revoke_removes_only_temporary_key_and_secret_bytes(monkeypatch):
    monkeypatch.setattr(a.Client, 'discover', lambda self: None)
    broker, native = Broker(), Native()
    plan = invoke(broker, native, {})
    invoke(broker, native, {'apply': True, 'planHash': plan['planHash']})
    broker.secrets['stalwart-self-service-admin'] = {'metadata': {
        'labels': {'re8ch.com/credential-owner': 're8ch-self-service-management'},
        'annotations': {'re8ch.com/bootstrap-state': 'Ready', 're8ch.com/provider-origin': CONFIG['origin']}},
        'data': {'token': 'runtime-token-untouched'}}
    plan = invoke(broker, native, {'action': 'revoke'})
    result = invoke(broker, native, {'action': 'revoke', 'apply': True, 'planHash': plan['planHash']})
    assert result['state'] == 'Revoked'
    assert native.writes[-1] == ('ApiKey', {'accountId': 'Account-id', 'destroy': ['ApiKey-id']})
    assert not broker.secrets[a.SECRET]['data']
    assert broker.secrets['stalwart-self-service-admin']['data']['token'] == 'runtime-token-untouched'
    assert invoke(broker, native, {'action': 'revoke'})['changed'] is False

def test_timeout_after_native_destroy_recovers_without_destroy_replay(monkeypatch):
    monkeypatch.setattr(a.Client, 'discover', lambda self: None)
    broker, native = Broker(), Native()
    plan = invoke(broker, native, {})
    invoke(broker, native, {'apply': True, 'planHash': plan['planHash']})
    broker.secrets['stalwart-self-service-admin'] = {'metadata': {
        'labels': {'re8ch.com/credential-owner': 're8ch-self-service-management'},
        'annotations': {'re8ch.com/bootstrap-state': 'Ready', 're8ch.com/provider-origin': CONFIG['origin']}}}
    original = native.method
    def lost_response(obj, action, args):
        result = original(obj, action, args)
        if 'destroy' in args: raise OperationError('STALWART_PROVIDER_FAILED', 'unknown outcome')
        return result
    native.method = lost_response
    plan = invoke(broker, native, {'action': 'revoke'})
    with pytest.raises(OperationError): invoke(broker, native, {'action': 'revoke', 'apply': True, 'planHash': plan['planHash']})
    assert broker.secrets[a.SECRET]['metadata']['annotations']['re8ch.com/bootstrap-state'] == 'Revoking'
    cleanup = invoke(broker, native, {'action': 'revoke'})
    assert cleanup['plan']['revoking'] is True
    result = invoke(broker, native, {'action': 'revoke', 'apply': True, 'planHash': cleanup['planHash']})
    assert result['state'] == 'Revoked' and not broker.secrets[a.SECRET]['data']
    assert sum('destroy' in args for _, args in native.writes) == 1

def test_domain_discovery_is_bounded_read_only_and_needs_no_domain_id():
    native, broker = Native(), Broker()
    result = a.call(broker, {'action': 'discover'}, 'platform-admin', config={**CONFIG, 'domainId': ''}, client=native)
    assert result == {'domains': [{'id': 'domain', 'name': 'example.test', 'enable': True}], 'truncated': False}
    assert not native.writes and not broker.secrets
    with pytest.raises(OperationError): invoke(broker, native, {'action': 'discover', 'apply': True})


def test_disabled_or_wrong_domain_prevents_seed():
    native, broker = Native(), Broker()
    native.objects['Domain']['enable'] = False
    with pytest.raises(OperationError) as error: invoke(broker, native, {})
    assert error.value.code == 'AUTHORITY_DOMAIN_INVALID' and not native.writes
