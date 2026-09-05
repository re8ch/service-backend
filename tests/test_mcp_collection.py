import importlib.util
from pathlib import Path

import pytest


MODULE = Path(__file__).parents[1] / "operations/mcp-conformance/mcp_collection.py"
spec = importlib.util.spec_from_file_location("mcp_collection", MODULE)
collection = importlib.util.module_from_spec(spec); spec.loader.exec_module(collection)


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
