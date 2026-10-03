import io
import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from test_ops_mcp_tool_profiles import load_profile

RESOURCE = 'https://tools.service.re8ch.com/tenant-native/mcp'
METADATA = 'https://tools.service.re8ch.com/.well-known/oauth-protected-resource/tenant-native/mcp'
ISSUER = 'https://db-rw.re8ch.com/auth/v1'


def probe(issuer=ISSUER, challenge_metadata=METADATA):
    app = load_profile('observe')
    requests = []

    def request(req, **kwargs):
        requests.append(req.full_url)
        if req.full_url == RESOURCE:
            raise HTTPError(RESOURCE, 401, 'Unauthorized', {'WWW-Authenticate': f'Bearer resource_metadata="{challenge_metadata}"'}, None)
        value = ({'resource': RESOURCE, 'authorization_servers': [ISSUER]} if req.full_url == METADATA else
                 {'issuer': issuer, 'authorization_endpoint': ISSUER + '/oauth/authorize',
                  'token_endpoint': ISSUER + '/oauth/token', 'code_challenge_methods_supported': ['S256']})
        response = io.BytesIO(json.dumps(value).encode())
        response.status = 200
        return response

    with patch.dict(app.os.environ, {}, clear=True), patch.object(app.socket, 'getaddrinfo', return_value=[(None, None, None, None, ('127.0.0.1', 443))]), patch.object(app.socket, 'create_connection', return_value=MagicMock()), patch.object(app.ssl, 'create_default_context', return_value=MagicMock()), patch.object(app.urllib.request, 'urlopen', side_effect=request):
        result = app.get_external_publication_readiness()
    assert all(':8443' not in url for url in requests)
    assert RESOURCE in requests and METADATA in requests
    return result


def test_current_native_endpoint_passes_without_retired_toolhive_objects():
    assert probe()['publicationReady']


def test_wrong_issuer_fails_closed():
    assert 'oauthDiscoveryPKCE' in probe(issuer='https://old.example')['blockers']


def test_stale_challenge_metadata_fails_closed():
    assert 'unauthenticatedChallenge' in probe(challenge_metadata='https://old.example/mcp')['blockers']
