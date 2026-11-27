"""Real SDK/HTTP tests. No Sentry network or operational credential is used."""
import asyncio
import contextlib
from concurrent.futures import ThreadPoolExecutor
import http.server
import importlib.util
import json
import io
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest
import sentry_sdk
from sentry_sdk.transport import Transport, HttpTransport

sys.path.insert(0, str(Path(__file__).parents[1] / "operations/releases"))

import re8ch_observability as observation
from runtime import OperationRunner, OperationError
from cluster_execution_proxy import interactive_transport


CANARY = "never-export-token-body-person@example.invalid"


class MemoryTransport(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.events = []
        self.lock = threading.Lock()

    def capture_envelope(self, envelope):
        with self.lock:
            self.events.extend(item.payload.json for item in envelope.items
                               if item.headers.get("type") in {"event", "transaction"})


@pytest.fixture
def captured(monkeypatch):
    previous_client, previous_enabled = sentry_sdk.get_client(), observation._enabled
    previous_component = observation._configured_component
    monkeypatch.setenv("SENTRY_DSN", "http://test-public@example.invalid/1")
    monkeypatch.setenv("SENTRY_RELEASE", "re8ch-cloud-platform@" + "a" * 40)
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "test")
    transport = MemoryTransport()
    assert observation.initialize("cloud-platform", transport=transport)
    yield transport.events
    sentry_sdk.get_client().close(timeout=1)
    sentry_sdk.get_global_scope().set_client(previous_client)
    observation._enabled = previous_enabled
    observation._configured_component = previous_component


def incoming(trace="a" * 32, op="op_" + "1" * 32):
    return {"sentry-trace": trace + "-" + "b" * 16 + "-0",
            "X-Re8ch-Operation-Id": op, "Authorization": CANARY,
            "Cookie": CANARY, "baggage": "sentry-user_id=" + CANARY}


def test_real_sdk_concurrent_requests_are_isolated_and_sampled_without_pii(captured):
    barrier = threading.Barrier(2)

    def request(number):
        trace, op = str(number) * 32, "op_" + str(number) * 32
        with observation.request_scope("cloud-platform", incoming(trace, op)):
            sentry_sdk.set_user({"email": CANARY})
            sentry_sdk.set_extra("arguments", {"accessToken": CANARY})
            sentry_sdk.add_breadcrumb(message=CANARY, data={"body": CANARY})
            with observation.phase("cloud.dispatch"):
                barrier.wait(timeout=3)
                observation.failure(RuntimeError(CANARY))
                observation.failure(RuntimeError(CANARY))  # one logical error

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(request, [1, 2]))
    errors = [e for e in captured if e.get("type") != "transaction"]
    transactions = [e for e in captured if e.get("type") == "transaction"]
    assert len(errors) == len(transactions) == 2  # incoming sampled=0 does not suppress configured 100%
    for event in errors + transactions:
        trace = event["contexts"]["trace"]["trace_id"]
        assert event["tags"]["operation_id"] == "op_" + trace
        assert event["tags"]["component"] == "cloud-platform"
    assert CANARY not in json.dumps(captured)
    assert all(not ({"request", "user", "extra", "breadcrumbs", "threads", "server_name"} & e.keys()) for e in captured)


def test_invalid_trace_and_operation_headers_do_not_become_event_metadata(captured):
    bad = {"sentry-trace": CANARY + "\r\n", "X-Re8ch-Operation-Id": CANARY, "baggage": CANARY}
    with observation.request_scope("cloud-platform", bad):
        sent = observation.headers()
        assert observation.valid_trace(sent["sentry-trace"])
        assert observation.valid_operation(sent["X-Re8ch-Operation-Id"])
        assert "baggage" not in sent
        observation.failure(ValueError(CANARY))
    assert not observation.valid_trace("0" * 32 + "-" + "0" * 16 + "-1")
    assert CANARY not in json.dumps(captured)


def test_timeout_and_command_results_preserve_executor_semantics(captured):
    calls = []

    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output=CANARY, stderr=CANARY)

    with observation.request_scope("cloud-platform", incoming()):
        with pytest.raises(OperationError) as error:
            OperationRunner(execute).run(["kubectl", CANARY], target="fixture", timeout=3, stdin=CANARY)
        assert error.value.code == "BACKEND_TIMEOUT"
    assert len(calls) == 1 and calls[0][1]["timeout"] == 3
    assert calls[0][1]["input"] == CANARY
    events = [e for e in captured if e.get("type") != "transaction"]
    assert len(events) == 1 and events[0]["tags"]["error_category"] == "timeout"
    assert observation.valid_operation(events[0]["tags"]["backend_operation_id"])
    assert CANARY not in json.dumps(captured)


@contextlib.contextmanager
def serving(handler):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_real_loopback_threads_keep_trace_parent_and_operation_id(captured):
    seen = []

    class Broker(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append({"trace": self.headers.get("sentry-trace"),
                         "operation": self.headers.get("X-Re8ch-Operation-Id"),
                         "baggage": self.headers.get("baggage")})
            assert self.headers["Authorization"] == "Bearer " + CANARY
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_):
            pass

    with serving(Broker) as broker:
        binding = {"clusterRef": "fixture", "bindingDigest": "fixture",
                   "brokerURL": f"http://127.0.0.1:{broker.server_port}"}
        with observation.request_scope("cloud-platform", incoming()):
            with interactive_transport(binding, token_source=lambda: CANARY,
                                       trace_context=observation.current_carrier()) as config:
                url = json.loads(Path(config).read_text())["clusters"][0]["cluster"]["server"]
                with observation.phase("cloud.operation", backend_operation_id="op_" + "7" * 32):
                    with urllib.request.urlopen(url + "/version", timeout=3) as response:
                        assert response.status == 200
    assert len(seen) == 1 and seen[0]["operation"] == "op_" + "1" * 32
    assert seen[0]["trace"].startswith("a" * 32 + "-") and seen[0]["baggage"] is None
    parent = next(e for e in captured if e.get("transaction") == "cloud-platform.request")
    relay = next(e for e in captured if e.get("transaction") == "cloud-relay.request")
    operation = next(s for s in parent["spans"] if s["op"] == "cloud.operation")
    assert relay["contexts"]["trace"]["parent_span_id"] == operation["span_id"]
    assert CANARY not in json.dumps(captured)


def test_sdk_default_http_transport_is_asynchronous(captured, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    class SlowTransport(HttpTransport):
        def _send_envelope(self, envelope):
            entered.set()
            release.wait(timeout=3)

    sentry_sdk.get_client().close(timeout=1)
    assert observation.initialize("cloud-platform", transport=SlowTransport)
    try:
        start = time.monotonic()
        with observation.request_scope("cloud-platform", incoming()):
            observation.failure(RuntimeError(CANARY))
        assert time.monotonic() - start < 1
        assert entered.wait(timeout=2)
    finally:
        release.set()
        sentry_sdk.flush(timeout=2)  # Test shutdown only, never a business request.


def test_cancelled_request_is_not_replayed_and_uses_fixed_class(captured):
    with pytest.raises(asyncio.CancelledError):
        with observation.request_scope("cloud-platform", incoming()):
            with observation.phase("cloud.dispatch"):
                raise asyncio.CancelledError(CANARY)
    errors = [e for e in captured if e.get("type") != "transaction"]
    assert len(errors) == 1 and errors[0]["tags"]["error_category"] == "canceled"
    assert CANARY not in json.dumps(captured)


def test_tenant_http_auth_and_resolve_share_request_trace(captured, monkeypatch):
    path = Path(__file__).parents[1] / "clusters/re8ch/components/ai-operations/tenant_control_controller.py"
    spec = importlib.util.spec_from_file_location("tenant_control_sentry_test", path)
    controller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controller)
    with serving(controller.Handler) as server:
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/v1/resolve",
            data=json.dumps({"accessToken": CANARY}).encode(),
            headers={**incoming(), "X-Tenant-Caller": "not-an-authorized-caller"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=3)
        assert error.value.code == 401
    auth = next(e for e in captured if e.get("type") != "transaction")
    assert auth["tags"]["phase"] == "tenant.auth"
    assert auth["contexts"]["trace"]["trace_id"] == "a" * 32
    with observation.request_scope("tenant-control", incoming()):
        monkeypatch.setattr(controller, "KUBE", type("FixtureKube", (), {"list": lambda *_: []})())
        with pytest.raises(PermissionError):
            controller.resolve(CANARY, "")
    assert any(s["op"] == "tenant.resolve" for e in captured for s in e.get("spans", []))
    assert CANARY not in json.dumps(captured)


def test_metrics_have_only_fixed_labels_and_cover_long_operations(captured):
    with observation.request_scope("cloud-platform", incoming(), phase_name="cloud.mcp", method="POST"):
        with observation.phase("cloud.dispatch"):
            observation.failure(PermissionError(CANARY))
        observation.status(403)
    samples = [s for metric in observation.REGISTRY.collect() for s in metric.samples]
    http = [s for s in samples if s.name == "re8ch_toolchain_http_requests_total"]
    assert http and all(set(s.labels) == {"component", "environment", "phase", "method", "status_class", "outcome", "error_category"} for s in http)
    assert all(s.labels["outcome"] in {"success", "error"} for s in http)
    buckets = [s.labels["le"] for s in samples if s.name == "re8ch_toolchain_http_request_duration_seconds_bucket"]
    assert {"120.0", "300.0", "600.0", "1800.0", "3600.0"} <= set(buckets)
    build = [s for s in samples if s.name == "re8ch_toolchain_build_info"]
    assert build and all(set(s.labels) == {"component", "release", "repository", "environment"} for s in build)
    payload, content_type, status = observation.metrics()
    assert status == 200 and "text/plain" in content_type and CANARY.encode() not in payload
    assert all(not ({"trace_id", "operation_id", "tenant", "user", "path", "tool_name"} & s.labels.keys()) for s in samples)


def test_health_and_metrics_skip_business_red_and_transactions(captured):
    from server import Handler
    before = len(captured)
    with serving(Handler) as server:
        for path in ("/healthz", "/metrics"):
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}" + path, timeout=3) as response:
                assert response.status == 200
    assert len(captured) == before


def test_correlation_logger_is_pure_json_and_matches_real_sdk_event(captured):
    stream = io.StringIO()
    handler = observation.LOG.handlers[0]
    previous = handler.setStream(stream)
    try:
        with observation.request_scope("cloud-platform", incoming(), phase_name="cloud.mcp", method="POST"):
            observation.failure(RuntimeError(CANARY))
        line = stream.getvalue().strip()
    finally:
        handler.setStream(previous)
    value = json.loads(line)
    error = next(e for e in captured if e.get("type") != "transaction")
    assert value["sentryEventId"] == error["event_id"]
    assert value["operationId"] == "op_" + "1" * 32 and value["traceId"] == "a" * 32
    assert value["timestamp"].endswith("Z") and value["errorCategory"] == "internal"
    assert value["component"] == "cloud-platform" and value["environment"] == "test"
    assert observation.LOG.propagate is False and handler.formatter._fmt == "%(message)s"
    assert CANARY not in line


def test_native_release_metadata_keeps_artifact_source_and_operator_token_boundary():
    import yaml
    root = Path(__file__).parents[1]
    cloud = yaml.safe_load((root / ".github/workflows/cloud-platform-release.yml").read_text())
    tenant = yaml.safe_load((root / ".github/workflows/tenant-release.yml").read_text())
    pin = "getsentry/action-release@2e505a70ff06dac263413242aa3617fec90134e5"
    for workflow in (cloud, tenant):
        steps = workflow["jobs"]["finalize"]["steps"]
        metadata = next(s for s in steps if s.get("uses", "") == pin)
        assert "environment" not in metadata["with"]  # Build metadata cannot claim deployment.
        assert metadata["with"]["set_commits"] == "auto"
        assert metadata["env"]["SENTRY_AUTH_TOKEN"] == "${{ secrets.SENTRY_AUTH_TOKEN }}"
        assert "SENTRY_RELEASE_ENABLED" in metadata["if"]
        assert all("SENTRY_AUTH_TOKEN" not in s.get("env", {}) for s in steps if s is not metadata)
    csteps = cloud["jobs"]["finalize"]["steps"]
    metadata = next(s for s in csteps if s.get("uses") == pin)
    assert metadata["with"]["release"] == "re8ch-cloud-platform@${{ steps.source.outputs.sha }}"
    checkout = next(s for s in csteps if s.get("name") == "Check out certified source for Sentry commit association")
    assert checkout["with"]["ref"] == "${{ steps.source.outputs.sha }}"
    assert checkout["with"]["fetch-depth"] == 0
    assert metadata["with"]["working_directory"] == checkout["with"]["path"]
    tmeta = next(s for s in tenant["jobs"]["finalize"]["steps"] if s.get("uses") == pin)
    assert "inputs.reuse_image == ''" in tmeta["if"]  # No fabricated old-image build SHA.
    for path in ("operations/cloud-platform/re8ch_observability.py", "operations/cloud-platform/runtime.py",
                 "operations/cloud-platform/server.py", "operations/cloud-platform/cluster_execution_proxy.py",
                 "clusters/re8ch/components/ai-operations/tenant_control_controller.py"):
        assert "SENTRY_AUTH_TOKEN" not in (root / path).read_text()


def test_unwritten_response_is_not_success_status_and_all_methods(captured, monkeypatch):
    records = []
    monkeypatch.setattr(observation, 'log_record', records.append)
    for method in ('OPTIONS', 'CONNECT', 'TRACE'):
        with pytest.raises(asyncio.CancelledError):
            with observation.request_scope('cloud-platform', incoming(), method=method):
                raise asyncio.CancelledError()
        assert records[-1]['method'] == method
        assert records[-1]['status'] == 0
        assert records[-1]['outcome'] == 'error'
    assert 'status_class="none"' in observation.metrics()[0].decode()


def test_sdk_setup_failure_preserves_business_and_restores_request_scope(captured, monkeypatch):
    original = sentry_sdk.start_transaction
    def fail(*args, **kwargs):
        raise RuntimeError(CANARY)
    monkeypatch.setattr(sentry_sdk, 'start_transaction', fail)
    with observation.request_scope('cloud-platform', incoming('1' * 32)) as first:
        assert first.operation_id
        assert observation._CURRENT.get() is first
    assert observation._CURRENT.get() is None
    monkeypatch.setattr(sentry_sdk, 'start_transaction', original)
    with observation.request_scope('cloud-platform', incoming('2' * 32)):
        assert observation.headers()['sentry-trace'].startswith('2' * 32)
    assert observation._CURRENT.get() is None
    assert CANARY not in json.dumps(captured)


def test_managed_stack_metadata_retains_location_without_sensitive_values(captured):
    try:
        observation.status(CANARY)  # real managed-module failing frame
    except ValueError:
        pass  # no current request means no conversion
    with observation.request_scope('cloud-platform', incoming()):
        try:
            observation.status(CANARY)
        except ValueError as error:
            observation.failure(error)
    event = next(e for e in captured if e.get('type') != 'transaction')
    frames = event['exception']['values'][0]['stacktrace']['frames']
    assert any(f['filename'] == 'operations/cloud-platform/re8ch_observability.py' and f['function'] == 'status' for f in frames)
    assert all(set(f) == {'filename', 'function', 'lineno'} for f in frames)
    assert CANARY not in json.dumps(captured)
    assert '/Volumes/' not in json.dumps(captured)
