"""Real kubectl HTTP authentication and interactive broker lifecycle regression."""
import contextlib
import gzip
import http.client
import http.server
import json
import select
import sys
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[1] / "operations/releases"))

import cluster_execution_proxy
from runtime import CloudPlatform, Inventory, OperationRunner


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
        thread.join(timeout=2)


def test_actual_kubectl_http_auth_and_target_transport(tmp_path, monkeypatch):
    if not shutil.which("kubectl"):
        pytest.skip("real kubectl is required for this transport regression")
    token = tmp_path / "projected-token"
    token.write_text("synthetic-current-projection")
    seen, writes, attacker_calls = [], [], []
    current = None
    cancelled = threading.Event()

    class Quiet(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

    class Attacker(Quiet):
        def do_GET(self):
            attacker_calls.append(True)
            self.send_response(200)
            self.end_headers()

    with serving(Attacker) as attacker:
        class Broker(Quiet):
            def reply(self, value, code=200, *, compressed=False):
                body = json.dumps(value).encode()
                if compressed:
                    body = gzip.compress(body)
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                if compressed:
                    self.send_header("Content-Encoding", "gzip")
                self.end_headers()
                self.wfile.write(body)

            def authenticated(self):
                # Record only presence/result, never a token/header value.
                correct = self.headers.get("Authorization") == "Bearer " + token.read_text().strip()
                seen.append({"method": self.command, "path": self.path,
                             "authorizationPresent": bool(self.headers.get("Authorization")), "matchesProjection": correct})
                if not correct:
                    self.reply({"kind": "Status", "apiVersion": "v1", "reason": "Unauthorized", "code": 401}, 401)
                return correct

            def do_GET(self):
                if not self.authenticated():
                    return
                path = self.path.removeprefix("/k8s/pilot/exact-grant").split("?", 1)[0]
                if path == "/version":
                    self.reply({"gitVersion": "synthetic-atlantic"})
                elif path == "/api":
                    self.reply({"kind": "APIVersions", "apiVersion": "v1", "versions": ["v1"]})
                elif path == "/apis":
                    self.reply({"kind": "APIGroupList", "apiVersion": "v1", "groups": []})
                elif path == "/api/v1":
                    self.reply({"kind": "APIResourceList", "apiVersion": "v1", "groupVersion": "v1", "resources": [
                        {"name": "configmaps", "singularName": "configmap", "namespaced": True,
                         "kind": "ConfigMap", "verbs": ["get", "list", "patch", "delete", "watch"]}]})
                elif path.endswith("/large"):
                    self.reply({"payload": "compressible" * 8192}, compressed=True)
                elif path.endswith("/redirect"):
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{attacker.server_port}/steal")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif "watch=true" in self.path:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"type":"ADDED","object":{"kind":"ConfigMap"}}\n')
                    self.wfile.flush()
                    readable, _, _ = select.select([self.connection], [], [], 4)
                    if readable and not self.connection.recv(1):
                        cancelled.set()
                elif current is not None and path.endswith("/configmaps/probe"):
                    self.reply(current)
                else:
                    self.reply({"kind": "Status", "apiVersion": "v1", "reason": "NotFound", "code": 404}, 404)

            def do_PATCH(self):
                nonlocal current
                if not self.authenticated():
                    return
                assert self.path.startswith("/k8s/pilot/exact-grant/api/v1/namespaces/default/configmaps/probe?")
                body = self.rfile.read(int(self.headers["Content-Length"]))
                current = yaml.safe_load(body)
                current["metadata"].update(uid="synthetic-object-uid", resourceVersion=str(len(writes) + 1))
                writes.append(current["data"]["revision"])
                self.reply(current)

        with serving(Broker) as broker:
            binding = {"adapter": "among-broker", "clusterRef": "re8ch-atlantic", "bindingDigest": "b" * 64,
                       "brokerURL": f"http://127.0.0.1:{broker.server_port}/k8s/pilot/exact-grant"}
            # Counterfactual uses the exact previous production kubeconfig shape.
            old = tmp_path / "old-kubeconfig"
            old.write_text(json.dumps({"apiVersion": "v1", "kind": "Config", "clusters": [
                {"name": "old", "cluster": {"server": binding["brokerURL"]}}], "users": [
                {"name": "old", "user": {"tokenFile": str(token)}}], "contexts": [
                {"name": "old", "context": {"cluster": "old", "user": "old"}}], "current-context": "old"}))
            before = subprocess.run(["kubectl", "--kubeconfig", str(old), "get", "--raw=/version"], capture_output=True, text=True, timeout=10)
            assert before.returncode != 0 and seen[-1]["authorizationPresent"] is False
            seen.clear()
            # A target-bound config must also ignore the global main override.
            monkeypatch.setenv("KUBERNETES_SERVER_OVERRIDE", "https://must-not-be-used.invalid")
            monkeypatch.setattr(cluster_execution_proxy, "projected_token", lambda: token.read_text().strip())
            inventory = Inventory([Path(__file__).parent / "fixtures/synthetic-nodes.yaml"])
            service = CloudPlatform(inventory, OperationRunner(subprocess.run), kubeconfig="unused", ssh_config="unused")
            service.cluster_bindings = lambda: SimpleNamespace(resolve=lambda ref: binding if ref == "re8ch-atlantic" else None)
            with service.for_cluster("re8ch-atlantic", binding["bindingDigest"]) as (bound, _):
                config = Path(bound.kubeconfig)
                document = json.loads(config.read_text())
                assert document["users"][0]["user"] == {}
                assert "tokenFile" not in config.read_text() and str(token) not in config.read_text()
                assert token.read_text() not in config.read_text()
                base = document["clusters"][0]["cluster"]["server"]
                result = bound.kubectl_exec(["get", "--raw=/version"])
                assert result["exitCode"] == 0 and "synthetic-atlantic" in result["stdout"], result
                assert result["target"] == "re8ch-atlantic"
                # Rotation is observed on the next request, not snapshotted in config.
                token.write_text("synthetic-rotated-projection")
                assert bound.kubectl_exec(["get", "--raw=/version"])["exitCode"] == 0
                manifest = tmp_path / "object.yaml"
                for revision in ("1", "2"):
                    manifest.write_text(yaml.safe_dump({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {
                        "name": "probe", "namespace": "default"}, "data": {"revision": revision}}))
                    result = bound.kubectl_exec(["apply", "--server-side", "--field-manager=fixture-interactive", "--validate=false", "-f", str(manifest), "-o", "json"])
                    assert result["exitCode"] == 0, result
                    assert json.loads(result["stdout"])["data"]["revision"] == revision
                assert writes == ["1", "2"]  # No business PATCH replay.
                assert all(row["matchesProjection"] for row in seen)
                result = bound.kubectl_exec(["get", "--raw=/api/v1/large"])
                assert result["exitCode"] == 0 and len(json.loads(result["stdout"])["payload"]) > 4096, result
                with urllib.request.urlopen(base + "/api/v1/large") as response:
                    assert response.headers["Content-Encoding"] == "gzip"
                    assert len(json.loads(gzip.decompress(response.read()))["payload"]) > 4096
                with pytest.raises(urllib.error.HTTPError) as redirected:
                    urllib.request.urlopen(base + "/api/v1/redirect")
                assert redirected.value.code == 302 and not attacker_calls
                before_count = len(seen)
                local = urllib.parse.urlsplit(base)
                connection = http.client.HTTPConnection(local.hostname, local.port)
                connection.request("GET", f"http://127.0.0.1:{attacker.server_port}/steal")
                rejected = connection.getresponse()
                assert rejected.status == 400
                rejected.read()
                connection.close()
                assert len(seen) == before_count and not attacker_calls
                started = time.monotonic()
                with urllib.request.urlopen(base + "/api/v1/configmaps?watch=true") as response:
                    assert json.loads(response.readline())["type"] == "ADDED"
                    assert time.monotonic() - started < 2
                assert cancelled.wait(2), "caller disconnect did not cancel the upstream watch"
                before_count = len(seen)
                token.write_text("")
                with pytest.raises(urllib.error.HTTPError) as missing:
                    urllib.request.urlopen(base + "/version")
                assert missing.value.code == 401 and len(seen) == before_count
            assert not config.exists()
            with pytest.raises(urllib.error.URLError):
                urllib.request.urlopen(base + "/version", timeout=1)
