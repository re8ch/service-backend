import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest


MODULE = Path(__file__).parents[1] / "operations/mcp-conformance/mcp_collection.py"
spec = importlib.util.spec_from_file_location("mcp_collection", MODULE)
collection = importlib.util.module_from_spec(spec); spec.loader.exec_module(collection)


class Response:
    def __init__(self, payload=None, status=200, headers=None):
        self.payload = payload
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, *_args):
        return json.dumps(self.payload).encode() if self.payload is not None else b""


def test_client_preserves_streamable_http_session_and_initializes_it():
    requests = []
    responses = iter([
        Response({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18"}}, headers={"Mcp-Session-Id": "session-1"}),
        Response(status=202),
        Response({"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}),
    ])

    def open_request(request, timeout):
        requests.append((request, timeout))
        response = next(responses)
        response.fp = io.BytesIO(response.read())
        response.read = response.fp.read
        return response

    client = collection.Client("https://tools.example/mcp", "opaque-token", 10)
    with patch.object(collection.urllib.request, "urlopen", open_request):
        client.rpc("initialize")
        client.notify("notifications/initialized")
        assert client.rpc("tools/list") == {"tools": []}

    assert requests[0][0].headers.get("Mcp-session-id") is None
    assert requests[1][0].headers["Mcp-session-id"] == "session-1"
    assert requests[2][0].headers["Mcp-session-id"] == "session-1"
    assert "text/event-stream" in requests[0][0].headers["Accept"]


def test_poll_waits_for_terminal_result_and_returns_attempt_count():
    values = iter([
        {"content": [{"text": '{"state":"Running"}'}]},
        {"content": [{"text": '{"state":"Succeeded","artifact":{"digest":"sha256:abc"}}'}]},
    ])
    sleeps = []
    value, attempts = collection.call_with_poll(
        lambda: next(values),
        [{"path": "payload.state", "equals": "Succeeded"}, {"path": "payload.artifact.digest", "nonEmpty": True}],
        {"maxAttempts": 3, "intervalSeconds": 0.25},
        sleeps.append,
    )
    assert attempts == 2
    assert collection.select(value, "payload.artifact.digest") == "sha256:abc"
    assert sleeps == [0.25]


def test_poll_fails_when_terminal_assertion_never_passes():
    with pytest.raises(AssertionError, match="after 2 attempts"):
        collection.call_with_poll(
            lambda: {"content": [{"text": '{"state":"Failed"}'}]},
            [{"path": "payload.state", "equals": "Succeeded"}],
            {"maxAttempts": 2, "intervalSeconds": 0},
            lambda _seconds: None,
        )
