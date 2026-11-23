import contextlib
import http.server
import importlib
import io
import gzip
import select
import time
import json
import shutil
import subprocess
import threading
import sys
from pathlib import Path
import urllib.error
import urllib.request
from email.message import Message

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "operations/releases"))

from cluster_execution_proxy import BINDING_HEADER, PROOF_HEADER, proxy_request
from runtime import OperationError


@contextlib.contextmanager
def serving(handler):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_runner_actual_http_api_proxy_cloud_and_broker_keep_caller_and_target(monkeypatch):
    import cluster_transport
    import cluster_execution_proxy
    import server as cloud_server
    seen = []
    cancelled = threading.Event()
    proof = "Bearer synthetic-short-lived-actions-proof"
    digest = "b" * 64

    class Quiet(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

    class Broker(Quiet):
        def do_GET(self):
            seen.append(("broker", self.path, self.headers.get("Authorization")))
            if self.headers.get("Authorization") != proof:
                self.send_error(403)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if "large" in self.path:
                self.send_header("Content-Encoding", "gzip")
            self.end_headers()
            if "watch=true" in self.path:
                self.wfile.write(b'{"type":"ADDED","object":{"kind":"ConfigMap"}}\n')
                self.wfile.flush()
                readable, _, _ = select.select([self.connection], [], [], 4)
                if readable and not self.connection.recv(1): cancelled.set()
                return
            if "large" in self.path:
                self.wfile.write(gzip.compress(json.dumps({"payload": "compressible" * 8192}).encode()))
                return
            self.wfile.write(b'{"gitVersion":"synthetic-atlantic"}')

    with serving(Broker) as broker:
        binding = {"adapter": "among-broker", "clusterRef": "re8ch-atlantic", "bindingDigest": digest,
                   "brokerURL": f"http://127.0.0.1:{broker.server_port}/k8s/pilot/exact-grant"}
        class Resolver:
            def resolve_execution(self, ref, release_id):
                assert ref == "re8ch-atlantic"
                assert release_id == "scoped-release"
                return binding
        class Service:
            def cluster_bindings(self):
                return Resolver()
        real_proxy = cluster_execution_proxy.proxy_request
        def authenticate(value):
            assert value == proof
            return {"username": "synthetic-actions-caller"}
        monkeypatch.setattr(cluster_execution_proxy, "caller_identity", authenticate)
        with serving(cloud_server.Handler) as cloud:
            cloud.service = Service()
            class MainAPI(Quiet):
                def do_GET(self):
                    assert self.headers.get("Authorization") == proof
                    assert self.path.startswith(cluster_transport.CLOUD_PROXY + "re8ch-atlantic/")
                    # Kubernetes WithAuthentication removes Authorization.
                    headers = {k: v for k, v in self.headers.items() if k.lower() not in {"authorization", "host"}}
                    seen.append(("api", self.path, self.headers.get(PROOF_HEADER)))
                    suffix = self.path.split("/proxy", 1)[1]
                    request = urllib.request.Request(f"http://127.0.0.1:{cloud.server_port}" + suffix, headers=headers)
                    with urllib.request.urlopen(request) as response:
                        self.send_response(response.status)
                        for key in ("Content-Type", "Content-Encoding"):
                            if response.headers.get(key): self.send_header(key, response.headers[key])
                        self.end_headers()
                        from http_stream import copy_response
                        copy_response(response, self.connection, self.wfile)
            with serving(MainAPI) as api:
                monkeypatch.setattr(cluster_transport, "MAIN_API", f"http://127.0.0.1:{api.server_port}")
                with cluster_transport.transport("re8ch-atlantic", digest, lambda: proof[7:], release_id="scoped-release") as config:
                    document = json.loads(open(config).read())
                    assert proof[7:] not in json.dumps(document)
                    with urllib.request.urlopen(document["clusters"][0]["cluster"]["server"] + "/version") as response:
                        assert json.load(response)["gitVersion"] == "synthetic-atlantic"
                    if shutil.which("kubectl"):
                        result = subprocess.run(["kubectl", "--kubeconfig", config, "get", "--raw=/version"], capture_output=True, text=True, timeout=10)
                        assert result.returncode == 0, result.stderr
                        assert json.loads(result.stdout)["gitVersion"] == "synthetic-atlantic"
                    base = document["clusters"][0]["cluster"]["server"]
                    with urllib.request.urlopen(base + "/api/v1/large") as response:
                        assert response.headers["Content-Encoding"] == "gzip"
                        assert len(json.loads(gzip.decompress(response.read()))["payload"]) > 4096
                    started = time.monotonic()
                    with urllib.request.urlopen(base + "/api/v1/configmaps?watch=true") as response:
                        assert json.loads(response.readline())["type"] == "ADDED"
                        assert time.monotonic() - started < 2
                    assert cancelled.wait(3), "caller close did not cancel all upstream hops"
                    if shutil.which("kubectl"):
                        large = subprocess.run(["kubectl", "--kubeconfig", config, "get", "--raw=/api/v1/large"], capture_output=True, text=True, timeout=10)
                        assert large.returncode == 0, large.stderr
                        assert len(json.loads(large.stdout)["payload"]) > 4096
                        cancelled.clear()
                        process = subprocess.Popen(["kubectl", "--kubeconfig", config, "get", "--raw=/api/v1/configmaps?watch=true"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                        try:
                            ready, _, _ = select.select([process.stdout], [], [], 2)
                            assert ready, "kubectl watch did not receive the small event before EOF"
                            assert json.loads(process.stdout.readline())["type"] == "ADDED"
                        finally:
                            process.terminate()
                            process.wait(timeout=3)
                        assert cancelled.wait(3), "kubectl cancellation did not reach the broker"
                assert not __import__("pathlib").Path(config).exists()
    assert seen and all(item[2] == proof for item in seen)
    assert all("/version" in item[1] or "/api/v1/" in item[1] for item in seen)


@pytest.mark.parametrize("case", ["bad-digest", "wrong-cluster", "missing-proof", "outside-api", "path-traversal"])
def test_rejected_request_never_reaches_broker(case):
    calls = []
    class Resolver:
        def resolve_execution(self, ref, release_id):
            if ref != "re8ch-atlantic":
                raise OperationError("CLUSTER_BINDING_UNAVAILABLE", "unknown cluster")
            return {"adapter": "among-broker", "bindingDigest": "b" * 64, "brokerURL": "http://broker/k8s/pilot/grant"}
    class Service:
        def cluster_bindings(self):
            return Resolver()
    headers = {PROOF_HEADER: "Bearer fixture", BINDING_HEADER: "b" * 64, "X-Re8ch-Release-Id": "scoped-release"}
    path = "/k8s/re8ch-atlantic/version"
    if case == "bad-digest": headers[BINDING_HEADER] = "c" * 64
    if case == "wrong-cluster": path = "/k8s/unknown/version"
    if case == "missing-proof": headers[PROOF_HEADER] = ""
    if case == "outside-api": path = "/k8s/re8ch-atlantic/arbitrary"
    if case == "path-traversal": path = "/k8s/re8ch-atlantic/apis/%2e%2e/escape"
    def authenticate(value):
        if not value: raise OperationError("AUTHENTICATION_REQUIRED", "missing")
        return {"username": "fixture"}
    with pytest.raises(OperationError):
        proxy_request(Service(), "GET", path, headers, None, authenticate=authenticate,
                      open_url=lambda *a, **k: calls.append(a))
    assert calls == []
