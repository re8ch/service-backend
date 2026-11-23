"""Exercise the real kubectl against a finalizer-retaining local API."""
import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

from runtime import CloudPlatform, Inventory, OperationRunner


def test_delete_acknowledges_finalizer_retained_object_without_waiting(tmp_path, monkeypatch):
    if not shutil.which("kubectl"):
        pytest.skip("real kubectl is required for the finalizer contract test")
    monkeypatch.delenv("KUBERNETES_SERVER_OVERRIDE", raising=False)
    pod = {"apiVersion": "v1", "kind": "Pod", "metadata": {
        "name": "finalizing", "namespace": "default", "uid": "fixture-uid",
        "resourceVersion": "1", "finalizers": ["fixture.invalid/retain"]}}
    writes = []

    class API(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, value, status=200):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/api":
                self.reply({"kind": "APIVersions", "apiVersion": "v1", "versions": ["v1"]})
            elif path == "/apis":
                self.reply({"kind": "APIGroupList", "apiVersion": "v1", "groups": []})
            elif path == "/api/v1":
                self.reply({"kind": "APIResourceList", "apiVersion": "v1", "groupVersion": "v1",
                            "resources": [{"name": "pods", "singularName": "pod", "namespaced": True,
                                           "kind": "Pod", "verbs": ["get", "list", "delete", "watch"]}]})
            elif path == "/api/v1/namespaces/default/pods/finalizing":
                self.reply(pod)
            elif path == "/api/v1/namespaces/default/pods":
                self.reply({"apiVersion": "v1", "kind": "PodList", "metadata": {"resourceVersion": "2"}, "items": [pod]})
            else:
                self.reply({"kind": "Status", "apiVersion": "v1", "reason": "NotFound", "code": 404}, 404)

        def do_DELETE(self):
            assert urlsplit(self.path).path == "/api/v1/namespaces/default/pods/finalizing"
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            writes.append(self.path)
            pod["metadata"]["deletionTimestamp"] = "2026-01-01T00:00:00Z"
            self.reply(pod)

    api = ThreadingHTTPServer(("127.0.0.1", 0), API)
    thread = threading.Thread(target=api.serve_forever, daemon=True)
    thread.start()
    config = tmp_path / "kubeconfig"
    config.write_text(yaml.safe_dump({"apiVersion": "v1", "kind": "Config",
        "clusters": [{"name": "fixture", "cluster": {"server": "http://127.0.0.1:" + str(api.server_port)}}],
        "contexts": [{"name": "fixture", "context": {"cluster": "fixture", "user": "fixture"}}],
        "users": [{"name": "fixture", "user": {}}], "current-context": "fixture"}))

    def bounded_real_kubectl(argv, **kwargs):
        # The old synchronous wait must fail this test promptly instead of
        # waiting for the production operation runner's entire deadline.
        kwargs["timeout"] = 5
        return subprocess.run(argv, **kwargs)

    try:
        fixture = Path(__file__).parent / "fixtures/synthetic-nodes.yaml"
        service = CloudPlatform(Inventory([fixture]), OperationRunner(bounded_real_kubectl),
                                kubeconfig=str(config), ssh_config="unused")
        result = service.kube_resource("delete", "pods", namespace="default", name="finalizing")
        assert result["exitCode"] == 0, result
        assert result["deletionAccepted"] is True and result["completionObserved"] is False
        assert len(writes) == 1 and pod["metadata"]["finalizers"] == ["fixture.invalid/retain"]
        # A subsequent real read still observes the object terminating. The
        # acknowledgment is deliberately not a physical-completion assertion.
        actual = json.loads(service.kube_resource("get", "pods", namespace="default", name="finalizing")["stdout"])
        assert actual["metadata"]["uid"] == "fixture-uid" and actual["metadata"]["deletionTimestamp"]
    finally:
        api.shutdown()
        api.server_close()
        thread.join(timeout=2)
