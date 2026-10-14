import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

BASE = Path(__file__).parents[1] / "operations/api-conformance"
spec = importlib.util.spec_from_file_location("request_budget_test", BASE / "request_budget.py")
budget = importlib.util.module_from_spec(spec)
spec.loader.exec_module(budget)


def test_reserved_compensation_cannot_be_consumed_by_fuzz(tmp_path, monkeypatch):
    monkeypatch.setattr(budget.time, "sleep", lambda seconds: None)
    path = tmp_path / "counter"
    budget.initialize(path, 5, reserved=2)
    for count in range(1, 4):
        assert budget.take(path) == count
    with pytest.raises(budget.BudgetExceeded):
        budget.take(path)
    assert budget.take(path, compensation=True) == 4
    assert budget.take(path, compensation=True) == 5
    with pytest.raises(budget.BudgetExceeded):
        budget.take(path, compensation=True)
    assert set(json.loads(path.read_text())) == {"maximum", "reserved", "rate", "used", "next"}


def test_attempt_limit_shared_between_processes(tmp_path):
    path = tmp_path / "counter"
    budget.initialize(path, 4, rate=5)
    code = "import sys; sys.path.insert(0,sys.argv[1]); from request_budget import take; take(sys.argv[2])"
    children = [subprocess.Popen([sys.executable, "-c", code, str(BASE), str(path)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(8)]
    results = [child.wait() for child in children]
    assert results.count(0) == 4
    assert json.loads(path.read_text())["used"] == 4


def test_transport_guard_counts_repeated_attempts_and_disables_hidden_retry(tmp_path, monkeypatch):
    requests = pytest.importorskip("requests")
    import types
    monkeypatch.syspath_prepend(str(BASE))
    transport = types.ModuleType("schemathesis.transport.requests")
    package = types.ModuleType("schemathesis")
    package.transport = types.ModuleType("schemathesis.transport")
    package.transport.requests = transport
    monkeypatch.setitem(sys.modules, "schemathesis", package)
    monkeypatch.setitem(sys.modules, "schemathesis.transport", package.transport)
    monkeypatch.setitem(sys.modules, "schemathesis.transport.requests", transport)
    path = tmp_path / "counter"
    budget.initialize(path, 1)
    monkeypatch.setenv("RE8CH_REQUEST_BUDGET", str(path))
    called = []
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", lambda *a, **k: called.append(True))
    hook_spec = importlib.util.spec_from_file_location("budget_hooks_test", BASE / "budget_hooks.py")
    hooks = importlib.util.module_from_spec(hook_spec)
    hook_spec.loader.exec_module(hooks)
    adapter = requests.adapters.HTTPAdapter()
    adapter.send(None)
    with pytest.raises(RuntimeError, match="budget exhausted"):
        adapter.send(None)
    assert called == [True]
    class Session:
        def request(self, **kwargs):
            raise requests.ConnectionError("synthetic")
    with pytest.raises(requests.ConnectionError):
        transport._request_resending_on_dropped_connection(Session(), {})


def test_real_schemathesis_stops_before_fourth_http_attempt(tmp_path):
    binary = os.getenv("SCHEMATHESIS_TEST_BINARY")
    if not binary:
        pytest.skip("set SCHEMATHESIS_TEST_BINARY for pinned engine integration")
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")
        def log_message(self, *args):
            pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    path = tmp_path / "counter"
    budget.initialize(path, 3, rate=5)
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({"openapi": "3.0.3", "info": {"title": "test", "version": "1"},
        "paths": {"/ping": {"get": {"operationId": "ping", "parameters": [
            {"in": "query", "name": "n", "schema": {"type": "integer"}}],
            "responses": {"200": {"description": "ok"}}}}}}))
    config = tmp_path / "schemathesis.toml"
    config.write_text("[cache]\nenabled=false\n")
    try:
        result = subprocess.run([binary, "--config-file", str(config), "run", str(schema),
            "--url", f"http://127.0.0.1:{server.server_port}", "--phases", "fuzzing", "--mode", "positive",
            "--max-examples", "5", "--no-shrink", "--request-retries", "0", "--generation-database", "none"],
            env={**os.environ, "PYTHONPATH": str(BASE), "SCHEMATHESIS_HOOKS": "budget_hooks",
                 "RE8CH_REQUEST_BUDGET": str(path)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        assert result.returncode != 0
        assert len(received) == 3
        assert json.loads(path.read_text())["used"] == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
