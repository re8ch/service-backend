"""Small, allowlisted Sentry boundary for the Python control plane.

Only opaque operation/trace identifiers and fixed classifications leave the
process. Credentials, request metadata, arguments and results never enter SDK
events. Relay threads receive an explicit carrier rather than inherited scope.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import json
import logging
import os
import re
import subprocess
import socket
import ssl
import threading
import time
import uuid
import urllib.error
import sys
from datetime import datetime, timezone
from dataclasses import dataclass, field

try:
    import sentry_sdk
    from sentry_sdk.integrations.atexit import AtexitIntegration
except ImportError:  # Source-only tooling can run without the image dependency.
    sentry_sdk = None

try:
    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
    from prometheus_client.exposition import choose_encoder
except ImportError:
    CollectorRegistry = None

REGISTRY = CollectorRegistry() if CollectorRegistry else None
if REGISTRY:
    HTTP_LABELS = ["component", "environment", "phase", "method", "status_class", "outcome", "error_category"]
    HTTP_REQUESTS = Counter("re8ch_toolchain_http_requests_total", "Toolchain HTTP requests", HTTP_LABELS, registry=REGISTRY)
    HTTP_DURATION = Histogram("re8ch_toolchain_http_request_duration_seconds", "Toolchain HTTP request duration", HTTP_LABELS,
        buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1800, 3600), registry=REGISTRY)
    PHASE_DURATION = Histogram("re8ch_toolchain_phase_duration_seconds", "Fixed control-plane phase duration", ["component", "environment", "phase"], registry=REGISTRY)
    ERRORS = Counter("re8ch_toolchain_operation_errors_total", "Fixed control-plane error classifications", ["component", "environment", "phase", "error_category"], registry=REGISTRY)
    BUILD = Gauge("re8ch_toolchain_build_info", "Toolchain component release identity", ["component", "release", "repository", "environment"], registry=REGISTRY)

TRACE = re.compile(r"^[0-9a-f]{32}-[0-9a-f]{16}(?:-[01])?$")
OPERATION = re.compile(r"^op_(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$")
SERVICES = {"cloud-platform", "cloud-relay", "tenant-control"}
PHASES = {"http.request", "http.response", "cloud.auth", "cloud.binding", "cloud.dispatch",
          "cloud.operation", "cloud.broker", "cloud.caller", "tenant.auth", "tenant.resolve",
          "tenant.kubernetes", "tenant.reconcile", "cloud.mcp", "cloud.cluster_proxy", "tenant.api", "unknown"}
CATEGORIES = {"none", "timeout", "network", "connection_closed", "canceled", "authorization",
              "invalid_request", "http_client", "http_server", "command_exit", "internal"}
_CURRENT = contextvars.ContextVar("re8ch_request_diagnostic", default=None)
_enabled = False
_configured_component = None
LOG = logging.getLogger("re8ch.observability")
LOG.setLevel(logging.INFO)
LOG.propagate = False
if not LOG.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    LOG.addHandler(handler)


def log_record(record):
    try:
        LOG.info(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), **record}))
    except Exception:
        pass


def component(service):
    return "cloud-platform" if service == "cloud-relay" else service


def environment():
    return os.getenv("SENTRY_ENVIRONMENT", "production")


def metrics(accept=""):
    if REGISTRY is None:
        return b"# observability dependency unavailable\n", "text/plain; charset=utf-8", 503
    encoder, content_type = choose_encoder(accept)
    return encoder(REGISTRY), content_type, 200


def valid_trace(value):
    value = value.lower() if isinstance(value, str) else ""
    if TRACE.fullmatch(value) and int(value[:32], 16) and int(value[33:49], 16):
        return value
    return ""


def valid_operation(value):
    return value if isinstance(value, str) and OPERATION.fullmatch(value) else ""


def _tags(tags):
    tags = tags if isinstance(tags, dict) else {}
    result = {}
    for key in ("operation_id", "backend_operation_id"):
        if valid_operation(tags.get(key)):
            result[key] = tags[key]
    for key, allowed in (("service", SERVICES), ("component", {"cloud-platform", "tenant-control"}),
                         ("phase", PHASES), ("error_category", CATEGORIES)):
        if tags.get(key) in allowed:
            result[key] = tags[key]
    status = str(tags.get("http.status_code", ""))
    if re.fullmatch(r"[1-5][0-9]{2}", status):
        result["http.status_code"] = status
    return result


def sanitize_event(event, _hint=None):
    """Rebuild, do not blacklist: even an accidentally enriched SDK event is safe."""
    result = {k: event[k] for k in ("event_id", "timestamp", "start_timestamp", "type") if k in event}
    result.update(platform="python", release=os.getenv("SENTRY_RELEASE", "unversioned"),
                  environment=os.getenv("SENTRY_ENVIRONMENT", "production"))
    tags = _tags(event.get("tags", {}))
    result["tags"] = tags
    trace = event.get("contexts", {}).get("trace", {})
    def safe_trace(value):
        data = {}
        for key, size in (("trace_id", 32), ("span_id", 16), ("parent_span_id", 16)):
            if isinstance(value.get(key), str) and re.fullmatch(r"[0-9a-f]{%d}" % size, value[key]):
                data[key] = value[key]
        if value.get("op") in PHASES | {"http.server"}:
            data["op"] = value["op"]
        if value.get("status") in {"ok", "internal_error", "unknown_error", "cancelled"}:
            data["status"] = value["status"]
        if value.get("origin") == "manual":
            data["origin"] = "manual"
        return data
    result["contexts"] = {"trace": safe_trace(trace)}
    if event.get("type") == "transaction":
        name = event.get("transaction")
        result["transaction"] = name if name in {s + ".request" for s in SERVICES} else "python.request"
        result["transaction_info"] = {"source": "custom"}
        spans = []
        for span in event.get("spans", []):
            if span.get("op") not in PHASES:
                continue
            value = {**safe_trace(span), **{k: span[k] for k in ("start_timestamp", "timestamp") if k in span}}
            value["description"] = span["op"]
            value["tags"] = _tags(span.get("tags", {}))
            duration = span.get("data", {}).get("duration_ms")
            if isinstance(duration, (int, float)):
                value["data"] = {"duration_ms": duration}
            spans.append(value)
        result["spans"] = spans
    else:
        category = tags.get("error_category", "internal")
        result.update(level="error", message="Control-plane operation failed",
                      fingerprint=[tags.get("service", "python"), tags.get("phase", "http.request"), category],
                      exception={"values": [{"type": category, "value": "Details intentionally omitted"}]})
    if event.get("type") != "transaction":
        frames = []
        for value in event.get("exception", {}).get("values", []):
            for frame in value.get("stacktrace", {}).get("frames", []):
                if (frame.get("filename") in MANAGED_FILES.values() and
                        isinstance(frame.get("function"), str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_<>]*", frame["function"]) and
                        isinstance(frame.get("lineno"), int) and frame["lineno"] > 0):
                    frames.append({k: frame[k] for k in ("filename", "function", "lineno")})
        if frames:
            result["exception"]["values"][0]["stacktrace"] = {"frames": frames}
    return result


def initialize(service, *, transport=None):
    global _enabled, _configured_component
    _configured_component = component(service)
    if REGISTRY:
        BUILD.labels(component(service), os.getenv("SENTRY_RELEASE", "unversioned"), "re8ch/service-backend", environment()).set(1)
    if not os.getenv("SENTRY_DSN") or sentry_sdk is None:
        _enabled = False
        log_record({"event": "observability-initialized", "component": component(service), "enabled": False,
                    "release": os.getenv("SENTRY_RELEASE", "unversioned"), "environment": environment()})
        return False
    try:
        sentry_sdk.init(dsn=os.environ["SENTRY_DSN"], release=os.getenv("SENTRY_RELEASE", "unversioned"),
            environment=os.getenv("SENTRY_ENVIRONMENT", "production"), server_name=service,
            default_integrations=False, auto_enabling_integrations=False, integrations=[AtexitIntegration()],
            data_collection={"user_info": False, "cookies": {"mode": "off"},
                "http_headers": {"request": {"mode": "off"}}, "http_bodies": [],
                "url_query_params": {"mode": "off"}, "database_query_data": False,
                "queues": False, "stack_frame_variables": False, "frame_context_lines": 0},
            include_local_variables=False, include_source_context=False,
            max_request_body_size="never", max_breadcrumbs=0, before_breadcrumb=lambda *_: None,
            sample_rate=1.0, traces_sampler=lambda _: 1.0, enable_logs=False, enable_backpressure_handling=False,
            before_send=sanitize_event, before_send_transaction=sanitize_event, transport=transport)
        _enabled = True
    except Exception:
        _enabled = False  # Telemetry setup cannot change business availability.
    log_record({"event": "observability-initialized", "component": component(service), "enabled": _enabled,
                "release": os.getenv("SENTRY_RELEASE", "unversioned"), "environment": environment()})
    return _enabled


@dataclass
class FailureCache:
    failures: set = field(default_factory=set)
    event_id: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class TraceCarrier:
    values: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    failure_cache: FailureCache = field(default_factory=FailureCache)
    backend_operation_id: str = ""

    def snapshot(self):
        with self.lock:
            return dict(self.values)

    def replace(self, values, backend_operation_id=""):
        with self.lock:
            self.values = dict(values)
            self.backend_operation_id = backend_operation_id


@dataclass
class RequestDiagnostic:
    service: str
    operation_id: str
    carrier: TraceCarrier
    route_phase: str = "http.request"
    method: str = "OTHER"
    http: bool = True
    status: int = 0
    phase: str = "http.request"
    backend_operation_id: str = ""

    @property
    def failures(self):
        with self.carrier.failure_cache.lock:
            return set(self.carrier.failure_cache.failures)

    @property
    def sentry_event_id(self):
        with self.carrier.failure_cache.lock:
            return self.carrier.failure_cache.event_id


def current_carrier():
    current = _CURRENT.get()
    return current.carrier if current else None


def headers(carrier=None):
    carrier = carrier or current_carrier()
    return carrier.snapshot() if carrier else {}


def category(error):
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
        return "timeout"
    if isinstance(error, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
        return "connection_closed"
    if isinstance(error, asyncio.CancelledError):
        return "canceled"
    if isinstance(error, PermissionError):
        return "authorization"
    if isinstance(error, (ValueError, TypeError, KeyError)):
        return "invalid_request"
    if isinstance(getattr(error, "code", None), int) and 400 <= error.code <= 599:
        return "authorization" if error.code in {401, 403} else "http_server" if error.code >= 500 else "http_client"
    if isinstance(error, urllib.error.URLError):
        return "timeout" if isinstance(error.reason, TimeoutError) else "network"
    if isinstance(error, (ConnectionError, socket.gaierror, ssl.SSLError)):
        return "network"
    if getattr(error, "code", None) in {"AUTHENTICATION_REQUIRED", "AUTHORIZATION_DENIED"}:
        return "authorization"
    if getattr(error, "code", None) in {"BACKEND_TIMEOUT"}:
        return "timeout"
    if getattr(error, "code", None) in {"INVALID_ARGUMENT", "CLUSTER_BINDING_CHANGED", "TARGET_NOT_FOUND", "METHOD_NOT_FOUND"}:
        return "invalid_request"
    if getattr(error, "code", None) == "CLUSTER_TRANSPORT_UNAVAILABLE":
        return "network"
    return "internal"


MANAGED_FILES = {"server.py": "operations/cloud-platform/server.py", "runtime.py": "operations/cloud-platform/runtime.py",
    "cluster_execution_proxy.py": "operations/cloud-platform/cluster_execution_proxy.py",
    "tenant_control_controller.py": "clusters/re8ch/components/ai-operations/tenant_control_controller.py",
    "re8ch_observability.py": "operations/cloud-platform/re8ch_observability.py"}


def safe_frames(error):
    frames = []
    tb = getattr(error, "__traceback__", None)
    while tb is not None:
        code = tb.tb_frame.f_code
        # Match the actual imported module's file, never an exception-provided path.
        module = sys.modules.get(tb.tb_frame.f_globals.get("__name__", ""))
        actual_file = getattr(module, "__file__", None)
        filename = os.path.basename(code.co_filename)
        if actual_file == code.co_filename and filename in MANAGED_FILES:
            frames.append({"filename": MANAGED_FILES[filename], "function": code.co_name, "lineno": tb.tb_lineno})
        tb = tb.tb_next
    return frames


def failure(error=None, *, error_category=None, phase_name=None):
    current = _CURRENT.get()
    if current is None:
        return
    classification = error_category or category(error)
    classification = classification if classification in CATEGORIES else "internal"
    name = phase_name or current.phase
    name = name if name in PHASES else "http.request"
    # The carrier shares only this opaque, fixed cache with request-local relay
    # threads. One failure crossing relay -> subprocess -> HTTP is one event.
    cache = current.carrier.failure_cache
    with cache.lock:
        if cache.failures:
            return
        cache.failures.add((name, classification))
        if REGISTRY:
            ERRORS.labels(component(current.service), environment(), name, classification).inc()
        if _enabled:
            tags = {"service": current.service, "component": component(current.service), "operation_id": current.operation_id,
                    "phase": name, "error_category": classification}
            if current.backend_operation_id:
                tags["backend_operation_id"] = current.backend_operation_id
            try:
                cache.event_id = sentry_sdk.capture_event({"level": "error", "tags": tags, "exception": {"values": [{"stacktrace": {"frames": safe_frames(error)}}]}}) or ""
            except Exception:
                pass


def status(code):
    current = _CURRENT.get()
    if current:
        current.status = int(code)


@contextlib.contextmanager
def request_scope(service, incoming=None, *, phase_name="http.request", method="OTHER", http=True, parent_carrier=None):
    service = service if service in SERVICES else "cloud-platform"
    incoming = incoming or {}
    operation_id = valid_operation(incoming.get("X-Re8ch-Operation-Id")) or "op_" + uuid.uuid4().hex
    trace_header = valid_trace(incoming.get("sentry-trace"))
    carrier = TraceCarrier({"X-Re8ch-Operation-Id": operation_id})
    if parent_carrier:
        carrier.failure_cache = parent_carrier.failure_cache
        carrier.backend_operation_id = parent_carrier.backend_operation_id
    current = RequestDiagnostic(service, operation_id, carrier, route_phase=phase_name,
                                method=method if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CONNECT", "TRACE"} else "OTHER",
                                http=http, backend_operation_id=carrier.backend_operation_id)
    token = _CURRENT.set(current)
    started = time.monotonic()
    stack = contextlib.ExitStack()
    transaction = None
    trace_id = trace_header[:32] if trace_header else uuid.uuid4().hex
    carrier.values["sentry-trace"] = trace_id + "-" + uuid.uuid4().hex[:16] + "-1"
    try:
        if _enabled:
            try:
                scope = stack.enter_context(sentry_sdk.isolation_scope())
                scope.clear()
                # Deliberately never hand arbitrary request headers/baggage to SDK.
                transaction = sentry_sdk.continue_trace({"sentry-trace": trace_header} if trace_header else {},
                    name=service + ".request", op="http.server", source="custom")
                transaction = stack.enter_context(sentry_sdk.start_transaction(transaction))
                transaction.set_tag("operation_id", operation_id)
                transaction.set_tag("service", service)
                transaction.set_tag("component", component(service))
                transaction.set_tag("phase", phase_name)
                carrier.values["sentry-trace"] = transaction.to_traceparent()
            except Exception:
                transaction = None
                try:
                    stack.close()
                except Exception:
                    pass
        try:
            yield current
        except BaseException as error:
            if not isinstance(error, (KeyboardInterrupt, SystemExit)):
                failure(error)
            raise
        finally:
            if current.status >= 400 and not current.failures:
                failure(error_category="http_server" if current.status >= 500 else
                        "authorization" if current.status in {401, 403} else "http_client",
                        phase_name=("tenant.auth" if service == "tenant-control" else "cloud.broker" if service == "cloud-relay" else "cloud.auth")
                        if current.status in {401, 403} else None)
            try:
                if transaction is not None:
                    transaction.set_tag("http.status_code", str(current.status))
                    transaction.set_tag("error_category", "none" if not current.failures else next(iter(current.failures))[1])
                    transaction.set_status("internal_error" if current.failures else "ok")
            except Exception:
                pass
            elapsed = round((time.monotonic() - started) * 1000, 3)
            error_category = "none" if not current.failures else next(iter(current.failures))[1]
            outcome = "success" if not current.failures else "error"
            if REGISTRY and current.http:
                labels = component(service), environment(), current.route_phase, current.method, str(current.status // 100) + "xx" if current.status else "none", outcome, error_category
                HTTP_REQUESTS.labels(*labels).inc()
                HTTP_DURATION.labels(*labels).observe(elapsed / 1000)
            record = {"event": "control-plane-request", "component": component(service), "phase": current.route_phase,
                      "operationId": operation_id, "traceId": carrier.snapshot()["sentry-trace"][:32],
                      "sentryEventId": current.sentry_event_id, "release": os.getenv("SENTRY_RELEASE", "unversioned"),
                      "environment": environment(), "method": current.method,
                      "status": current.status, "durationMs": elapsed, "outcome": outcome,
                      "errorCategory": error_category}
            log_record(record)


    finally:
        try:
            stack.close()
        except Exception:
            pass
        finally:
            _CURRENT.reset(token)


@contextlib.contextmanager
def phase(name, backend_operation_id=""):
    current = _CURRENT.get()
    if current is None:
        yield
        return
    name = name if name in PHASES else "http.request"
    previous = current.phase, current.backend_operation_id, current.carrier.snapshot()
    current.phase = name
    if valid_operation(backend_operation_id):
        current.backend_operation_id = backend_operation_id
    started = time.monotonic()
    with contextlib.ExitStack() as stack:
        span = None
        try:
            if _enabled:
                span = stack.enter_context(sentry_sdk.start_span(op=name, name=name))
                span.set_tag("operation_id", current.operation_id)
                span.set_tag("service", current.service)
                span.set_tag("component", component(current.service))
                span.set_tag("phase", name)
                if current.backend_operation_id:
                    span.set_tag("backend_operation_id", current.backend_operation_id)
                current.carrier.replace({**previous[2], "sentry-trace": span.to_traceparent()}, current.backend_operation_id)
        except Exception:
            span = None
        try:
            yield
        except BaseException as error:
            if not isinstance(error, (KeyboardInterrupt, SystemExit)):
                failure(error)
            raise
        finally:
            duration = time.monotonic() - started
            if REGISTRY:
                PHASE_DURATION.labels(component(current.service), environment(), name).observe(duration)
            try:
                if span is not None:
                    span.set_data("duration_ms", round(duration * 1000, 3))
            except Exception:
                pass
            current.phase, current.backend_operation_id = previous[:2]
            current.carrier.replace(previous[2], previous[1])


def observed_phase(name, *, service=None):
    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            if service and _CURRENT.get() is None and _configured_component != component(service):
                # Other source consumers importing the controller do not become
                # a telemetry producer merely by importing a shared class.
                return function(*args, **kwargs)
            scope = request_scope(service, phase_name=name, http=False) if service and _CURRENT.get() is None else contextlib.nullcontext()
            with scope, phase(name):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def observed_http(service):
    def decorate(function):
        @functools.wraps(function)
        def wrapped(handler, *args, **kwargs):
            path = handler.path
            # Technical probes and self-scrapes do not dilute business RED.
            if path in {"/metrics", "/healthz", "/livez", "/readyz"}:
                return function(handler, *args, **kwargs)
            route = ("tenant.resolve" if path in {"/v1/resolve", "/v1/resolve-supabase"}
                     else "tenant.api" if service == "tenant-control" and path.startswith("/v1/")
                     else "cloud.mcp" if path == "/mcp"
                     else "cloud.cluster_proxy" if path.startswith("/k8s/")
                     else "cloud.binding" if path.startswith("/cluster-bindings/") else "unknown")
            with request_scope(service, handler.headers, phase_name=route,
                    method=getattr(handler, "command", function.__name__.removeprefix("do_"))):
                return function(handler, *args, **kwargs)
        return wrapped
    return decorate
