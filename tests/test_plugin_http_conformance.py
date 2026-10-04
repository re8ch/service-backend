import copy
import json
import socketserver
import threading
from http.server import BaseHTTPRequestHandler

import pytest

from runtime import OperationError
from plugin_http_conformance import check_response, conformance, require_local_refs
import plugin_http_transport as transport
from test_plugin_capabilities import ResourceService, read_args

DOCUMENT = {'openapi': '3.0.0', 'info': {'title': 'fixture', 'version': '1'}, 'paths': {
    '/apis/test/v1/items': {'get': {'responses': {
        '200': {'description': 'OK', 'headers': {'X-Count': {'required': True, 'schema': {'type': 'integer'}}},
                'content': {'application/json': {'schema': {'type': 'object', 'required': ['items'],
                    'properties': {'items': {'type': 'array', 'items': {'type': 'string'}}}}}}},
        '401': {'description': 'Unauthorized'}}}}}}
CONTRACT = {'pathTemplate': '/apis/test/v1/items'}
PARAMETERS = {'path': {}, 'query': {}}


def response(**kw):
    return {'status': 200, 'headers': {'Content-Type': 'application/json', 'X-Count': '1'},
            'body': b'{"items":["fixture"]}', **kw}


def test_real_schemathesis_checks_accept_complete_response_without_returning_payload():
    report = check_response(DOCUMENT, CONTRACT, PARAMETERS, response())
    assert report['engine'] == 'schemathesis'
    assert report['httpConformance'] == 'passed'
    assert len(report['checks']) == 4
    assert report['networkRequests'] == 1
    assert report['semanticConformance'] == 'not_run'
    assert 'fixture' not in json.dumps(report)


@pytest.mark.parametrize('changed, failed_check', [
    ({'status': 299}, 'status_code_conformance'),
    ({'headers': {'Content-Type': 'text/plain', 'X-Count': '1'}}, 'content_type_conformance'),
    ({'headers': {'Content-Type': 'application/json'}}, 'response_headers_conformance'),
    ({'body': b'{"items": "sensitive-body-do-not-return"}'}, 'response_schema_conformance'),
])
def test_http_contract_violations_are_failed_without_sensitive_diagnostics(changed, failed_check):
    report = check_response(DOCUMENT, CONTRACT, PARAMETERS, response(**changed))
    assert report['httpConformance'] == 'failed'
    assert {'check': failed_check, 'status': 'failed'} in report['checks']
    assert 'sensitive-body' not in json.dumps(report)


def test_documented_authentication_failure_is_not_read_coverage():
    assert check_response(DOCUMENT, CONTRACT, PARAMETERS, response(status=401))['httpConformance'] == 'failed'


@pytest.mark.parametrize('ref', ['https://example.invalid/schema', 'file:///etc/passwd', '../schema.json', 3])
def test_remote_references_are_rejected_before_schemathesis(ref):
    with pytest.raises(OperationError, match='document-local'):
        require_local_refs({'nested': [{'$ref': ref}]})


def test_conformance_uses_same_bound_read_contract_and_blocks_watch_and_stale_schema():
    service = ResourceService()
    args = read_args(service)
    service.http_calls = []
    def http_get(path):
        service.http_calls.append(path)
        return {**response(body=b'{"items":[]}'), 'auditRef': 'audit://fixture'}
    service._plugin_http_get = http_get
    result = conformance(service, **args)
    assert result['httpConformance'] == 'passed'
    assert service.http_calls == ['/apis/anatomy.re8ch.com/v1alpha1/namespaces/acceptance/runtimecommits?limit=100&timeoutSeconds=10']
    assert result['auditRef'] == 'audit://fixture'
    before = len(service.http_calls)
    with pytest.raises(OperationError):
        conformance(service, **{**args, 'schema_hash': '0' * 64})
    with pytest.raises(OperationError):
        conformance(service, **{**args, 'parameters': {'path': {'namespace': 'acceptance'}, 'query': {'watch': True}}})
    assert len(service.http_calls) == before


def test_non_admin_cannot_discover_or_invoke_http_acceptance():
    import server
    from runtime import Identity
    viewer = Identity('viewer', ())
    assert 'plugin.operation.http-check' not in {t['name'] for t in server.tool_list(viewer)}
    with pytest.raises(OperationError) as error:
        server.dispatch(object(), 'plugin.operation.http-check', {}, viewer)
    assert error.value.code == 'AUTHORIZATION_DENIED'


def test_private_unix_http_transport_preserves_status_headers_and_cleans_up(monkeypatch):
    processes = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(206)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', '12')
            self.end_headers()
            self.wfile.write(b'{"items":[]}')
        def log_message(self, *args):
            pass
    class Proxy:
        def __init__(self, argv, **kw):
            assert '--unix-socket' in argv
            assert '--accept-paths=^/apis/test/v1/items$' in argv
            assert '--accept-hosts=^localhost$' in argv
            assert not any(arg.startswith('--address') for arg in argv)
            self.path = argv[argv.index('--unix-socket') + 1]
            self.server = socketserver.UnixStreamServer(self.path, Handler)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            self.stopped = False
            processes.append(self)
        def poll(self):
            return None
        def terminate(self):
            if not self.stopped:
                self.server.shutdown()
                self.server.server_close()
                self.stopped = True
        kill = terminate
        def wait(self, timeout):
            self.thread.join(timeout)
    monkeypatch.setattr(transport.subprocess, 'Popen', Proxy)
    result = transport.get('/reference-only', '/apis/test/v1/items?limit=1')
    assert result['status'] == 206
    assert result['headers']['content-type'] == ['application/json']
    assert result['body'] == b'{"items":[]}'
    assert processes[-1].stopped
    monkeypatch.setattr(transport, 'MAX_BODY', 3)
    with pytest.raises(OperationError) as error:
        transport.get('/reference-only', '/apis/test/v1/items')
    assert error.value.code == 'PLUGIN_HTTP_TOO_LARGE'
    assert processes[-1].stopped


@pytest.mark.parametrize('path', ['https://remote/apis/x', '//remote/apis/x', '/api/v1/secrets', '/apis/x/../secrets', '/apis/x\r\nHost: remote'])
def test_transport_rejects_unbound_targets_without_starting_proxy(path, monkeypatch):
    monkeypatch.setattr(transport.subprocess, 'Popen', lambda *a, **kw: pytest.fail('proxy must not start'))
    with pytest.raises(OperationError):
        transport.get('/reference-only', path)


def test_actual_kubectl_proxy_and_schemathesis_http_roundtrip(tmp_path):
    import shutil
    from http.server import ThreadingHTTPServer
    if not shutil.which('kubectl'):
        pytest.skip('kubectl unavailable; runtime image smoke also checks packaged transport')
    paths = []
    class API(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            payload = b'{"items":[]}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('X-Count', '0')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *args):
            pass
    api = ThreadingHTTPServer(('127.0.0.1', 0), API)
    thread = threading.Thread(target=api.serve_forever, daemon=True)
    thread.start()
    kubeconfig = tmp_path / 'fixture.yaml'
    kubeconfig.write_text(json.dumps({'apiVersion': 'v1', 'kind': 'Config',
        'clusters': [{'name': 'fixture', 'cluster': {'server': f'http://127.0.0.1:{api.server_port}'}}],
        'contexts': [{'name': 'fixture', 'context': {'cluster': 'fixture'}}], 'current-context': 'fixture'}))
    try:
        actual = transport.get(str(kubeconfig), '/apis/test/v1/items?limit=1')
        report = check_response(DOCUMENT, CONTRACT, PARAMETERS, actual)
        assert report['httpConformance'] == 'passed'
        assert paths == ['/apis/test/v1/items?limit=1']
    finally:
        api.shutdown()
        api.server_close()
        thread.join(2)


def test_http_acceptance_serializes_cases_and_releases_slot_after_invalid_request():
    import plugin_http_conformance as module
    module.SLOT.acquire()
    try:
        with pytest.raises(OperationError) as error:
            module.conformance(object(), '', '', '')
        assert error.value.code == 'PLUGIN_HTTP_BUSY'
    finally:
        module.SLOT.release()
    with pytest.raises(OperationError):
        module.conformance(object(), '', '', '')
    assert module.SLOT.acquire(blocking=False)
    module.SLOT.release()
