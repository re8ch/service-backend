"""Request-scoped Kubernetes transports; remote credentials stay in the broker."""
from __future__ import annotations

import contextlib
import http.server
import json
import tempfile
import threading
from pathlib import Path

import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

from runtime import OperationError
from cluster_bindings import no_redirect_open
import re8ch_observability as observation

PROOF_HEADER = "X-Re8ch-Execution-Proof"
BINDING_HEADER = "X-Re8ch-Cluster-Binding"
RELEASE_HEADER = "X-Re8ch-Release-Id"


@observation.observed_phase("cloud.caller")
def caller_identity(proof, open_url=no_redirect_open):
    """Native self-review authenticates the original caller without review RBAC.

    The broker subsequently performs its existing TokenReview and exact named
    grant-use SAR for this same proof on every forwarded request.
    """
    if not proof.startswith("Bearer ") or not proof[7:].strip():
        raise OperationError("AUTHENTICATION_REQUIRED", "execution caller proof is required")
    host = os.getenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    port = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")
    request = urllib.request.Request(f"https://{host}:{port}/apis/authentication.k8s.io/v1/selfsubjectreviews",
        data=json.dumps({"apiVersion": "authentication.k8s.io/v1", "kind": "SelfSubjectReview"}).encode(),
        method="POST", headers={"Authorization": proof, "Content-Type": "application/json"})
    request.headers.update(observation.headers())
    context = ssl.create_default_context(cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
    try:
        with open_url(request, context=context, timeout=30) as response:
            identity = json.load(response).get("status", {}).get("userInfo", {})
    except Exception:
        # Exception strings can contain headers; never return or log the proof.
        raise OperationError("AUTHENTICATION_REQUIRED", "execution caller authentication failed") from None
    if not identity.get("username") or identity["username"] == "system:anonymous":
        raise OperationError("AUTHENTICATION_REQUIRED", "execution caller authentication failed")
    return identity


def _relative_target(target):
    parsed = urllib.parse.urlsplit(target)
    decoded = urllib.parse.unquote(parsed.path)
    if (parsed.scheme or parsed.netloc or not parsed.path.startswith("/")
            or ".." in decoded.split("/") or "\r" in decoded or "\n" in decoded):
        raise OperationError("INVALID_ARGUMENT", "a relative Kubernetes API path is required")
    return parsed


@observation.observed_phase("cloud.broker")
def _broker_request(binding, method, target, headers, body, proof, *, open_url=no_redirect_open):
    parsed = _relative_target(target)
    url = binding["brokerURL"] + parsed.path + (("?" + parsed.query) if parsed.query else "")
    forwarded = {key: headers[key] for key in ("Content-Type", "Accept", "Accept-Encoding") if headers.get(key)}
    forwarded["Authorization"] = proof
    forwarded.update(observation.headers())
    request = urllib.request.Request(url, data=body, method=method, headers=forwarded)
    try:
        return open_url(request, timeout=120)
    except urllib.error.HTTPError as error:
        return error
    except Exception:
        raise OperationError("CLUSTER_TRANSPORT_UNAVAILABLE", "managed cluster transport failed", retryable=True) from None


def projected_token():
    return Path("/var/run/secrets/kubernetes.io/serviceaccount/token").read_text().strip()


@contextlib.contextmanager
def interactive_transport(binding, token_source=None, trace_context=None):
    """Native kubectl omits AuthInfo for HTTP. Inject this Pod's identity in memory.

    The reviewed broker still authenticates it with TokenReview and exact grant
    use SAR. No remote credential or local bearer enters kubectl's config/argv.
    """
    from http_stream import copy_response
    token_source = token_source or projected_token

    class Handler(http.server.BaseHTTPRequestHandler):
        def forward(self):
            # ThreadingHTTPServer does not inherit the caller's ContextVars.
            # This carrier is request-local and refreshed by the command span.
            with observation.request_scope("cloud-relay", observation.headers(trace_context),
                    phase_name="cloud.cluster_proxy", method=self.command, http=False, parent_carrier=trace_context):
                return self.forward_scoped()

        def forward_scoped(self):
            started = False
            try:
                _relative_target(self.path)
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0:
                    raise ValueError()
                body = self.rfile.read(length) if length else None
                if body is not None and len(body) != length:
                    raise ValueError()
                token = token_source()
                if not token:
                    self.send_error(401, "projected caller identity unavailable")
                    return
                response = _broker_request(binding, self.command, self.path, self.headers, body, "Bearer " + token)
                with response:
                    observation.status(response.status)
                    self.send_response(response.status)
                    for key in ("Content-Type", "Content-Encoding", "Warning", "Audit-Id", "WWW-Authenticate"):
                        for value in response.headers.get_all(key, []):
                            self.send_header(key, value)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    started = True
                    with observation.phase("http.response"):
                        copy_response(response, self.connection, self.wfile)
                print(json.dumps({"event": "managed-cluster-interactive-request",
                    "clusterRef": binding["clusterRef"], "clusterBindingDigest": binding["bindingDigest"],
                    "method": self.command, "status": response.status}))
            except OperationError as error:
                observation.failure(error)
                if not started:
                    self.send_error(400 if error.code == "INVALID_ARGUMENT" else 502, "managed cluster transport failed")
            except (BrokenPipeError, ConnectionResetError) as error:
                observation.failure(error)
            except (OSError, ValueError) as error:
                observation.failure(error)
                if not started:
                    self.send_error(502, "projected caller transport unavailable")
            finally:
                self.close_connection = True

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = forward

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="re8ch-interactive-target-") as directory:
            config = Path(directory) / "kubeconfig"
            config.write_text(json.dumps({"apiVersion": "v1", "kind": "Config",
                "clusters": [{"name": binding["clusterRef"], "cluster": {"server": f"http://127.0.0.1:{server.server_port}"}}],
                "users": [{"name": "request-local", "user": {}}],
                "contexts": [{"name": binding["clusterRef"], "context": {"cluster": binding["clusterRef"], "user": "request-local"}}],
                "current-context": binding["clusterRef"]}))
            config.chmod(0o600)
            yield str(config)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def proxy_request(service, method, target, headers, body, *, authenticate=caller_identity, open_url=no_redirect_open):
    parsed = _relative_target(target)
    parts = parsed.path.split("/", 3)
    if len(parts) != 4 or parts[1] != "k8s" or not parts[2]:
        raise OperationError("INVALID_ARGUMENT", "a reviewed cluster transport path is required")
    cluster_ref, path = parts[2], "/" + parts[3]
    decoded = urllib.parse.unquote(path)
    if (method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}
            or not (path.startswith(("/api/", "/apis/", "/openapi/")) or path in {"/api", "/apis", "/version", "/readyz", "/livez"})
            or ".." in decoded.split("/") or "\r" in decoded or "\n" in decoded):
        raise OperationError("INVALID_ARGUMENT", "only Kubernetes API paths are accepted")
    proof = headers.get(PROOF_HEADER, "")
    identity = authenticate(proof)
    release_id = headers.get(RELEASE_HEADER, "")
    if not release_id:
        raise OperationError("INVALID_ARGUMENT", "scoped release identity is required")
    with observation.phase("cloud.binding"):
        binding = service.cluster_bindings().resolve_execution(cluster_ref, release_id)
    if binding["adapter"] != "among-broker":
        raise OperationError("INVALID_ARGUMENT", "self cluster uses its existing direct API transport")
    if headers.get(BINDING_HEADER) != binding["bindingDigest"]:
        raise OperationError("CLUSTER_BINDING_CHANGED", "execution binding differs from the reviewed cluster")
    response = _broker_request(binding, method, path + (("?" + parsed.query) if parsed.query else ""),
                               headers, body, proof, open_url=open_url)
    # No local or remote credential material is included in this audit envelope.
    audit = {"clusterRef": cluster_ref, "clusterBindingDigest": binding["bindingDigest"],
             "releaseId": release_id,
             "caller": identity["username"], "method": method, "status": response.status}
    return response, audit
