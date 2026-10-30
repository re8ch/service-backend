import copy
import json
import pytest
import stalwart_bootstrap as b
from runtime import OperationError, Identity

CONFIG = {'origin': 'https://provider.example.test', 'domainId': 'domain', 'revision': 'reviewed-sha', 'tokenFile': '/mounted/bootstrap'}

class Provider:
    endpoint = CONFIG['origin']
    def __init__(self):
        self.objects = {}; self.writes = []; self.scopes = list(b.CLIENT['scopes'])
    def discover(self): pass
    def request(self, path):
        return {'scopes_supported': ['urn:ietf:params:oauth:scope:' + x for x in self.scopes]}
    def method(self, obj, action, args):
        if action == 'query': return {'ids': [self.objects[obj]['id']] if obj in self.objects else []}
        if action == 'get': return {'list': [copy.deepcopy(self.objects[obj])]}
        self.writes.append((obj, args))
        self.objects[obj] = {**copy.deepcopy(args['create']['re8ch']), 'id': obj + '-id'}
        return {'created': {'re8ch': {'id': obj + '-id', 'secret': 'private-generated-key'}}}

class Broker:
    def __init__(self): self.secret = None; self.fail_save = False
    def _kubectl(self, argv, stdin=None):
        out = {'exitCode': 0, 'stdout': '', 'operationId': 'op', 'auditRef': 'audit://op'}
        if argv[0] == 'get': out['stdout'] = json.dumps(self.secret) if self.secret else ''
        elif argv[0] == 'create':
            if self.secret: out.update(exitCode=1, stderr='secret-value-could-be-echoed')
            else:
                self.secret = json.loads(stdin); self.secret['metadata']['resourceVersion'] = '1'
        elif argv[0] == 'replace':
            if self.fail_save: out.update(exitCode=1, stderr=stdin)
            else: self.secret = json.loads(stdin)
        return out

def invoke(broker, provider, args):
    return b.call(broker, args, 'platform-admin', configuration=CONFIG, client=provider)

def test_plan_is_nonmutating_and_apply_stores_only_new_restricted_key(monkeypatch):
    monkeypatch.setattr(b.Client, 'discover', lambda self: None)
    broker, provider = Broker(), Provider()
    plan = invoke(broker, provider, {})
    assert not provider.writes and not broker.secret
    result = invoke(broker, provider, {'apply': True, 'planHash': plan['planHash']})
    assert result['state'] == 'Ready'
    assert 'private-generated-key' not in json.dumps(result)
    assert set(provider.objects['ApiKey']['permissions']['permissions']) == set(b.PERMISSIONS)
    assert 'authenticate' in b.PERMISSIONS
    assert not any(x in b.PERMISSIONS for x in ['sysApiKeyCreate', 'sysOAuthClientCreate'])
    assert provider.objects['Account']['permissions']['@type'] == 'Replace'
    assert provider.objects['OAuthClient']['firstParty'] is False
    assert provider.objects['OAuthClient']['grantTypes'] == {'deviceCode': True}
    assert provider.writes[-1][1]['accountId'] == 'Account-id'
    assert broker.secret['data']['token'] == 'cHJpdmF0ZS1nZW5lcmF0ZWQta2V5'
    again = invoke(broker, provider, {'apply': True})
    assert again['changed'] is False and len(provider.writes) == 3

@pytest.mark.parametrize('failure', ['scope', 'plan', 'account', 'client', 'foreign-secret'])
def test_fail_closed_before_writes(failure):
    broker, provider = Broker(), Provider()
    args = {'apply': True, 'planHash': 'wrong'}
    if failure == 'scope': provider.scopes.remove('files')
    if failure == 'account': provider.objects['Account'] = {**b.account_value(CONFIG), 'id': 'x', 'roles': {'@type': 'Admin'}}
    if failure == 'client': provider.objects['OAuthClient'] = {**b.CLIENT, 'id': 'x', 'firstParty': True}
    if failure == 'foreign-secret': broker.secret = {'metadata': {}}
    with pytest.raises(OperationError): invoke(broker, provider, args)
    assert not provider.writes

def test_partial_write_is_not_replayed_and_error_never_contains_secret(monkeypatch):
    monkeypatch.setattr(b.Client, 'discover', lambda self: None)
    broker, provider = Broker(), Provider(); broker.fail_save = True
    plan = invoke(broker, provider, {})
    with pytest.raises(OperationError) as error:
        invoke(broker, provider, {'apply': True, 'planHash': plan['planHash']})
    assert 'private-generated-key' not in str(error.value)
    with pytest.raises(OperationError) as error: invoke(broker, provider, {})
    assert error.value.code == 'BOOTSTRAP_RECOVERY_REQUIRED'
    assert len(provider.writes) == 3

def test_tool_is_admin_only():
    import server
    with pytest.raises(OperationError) as error:
        server.dispatch(None, 'credentials.stalwart.ensure', {}, Identity('member', ()))
    assert error.value.code == 'AUTHORIZATION_DENIED'
