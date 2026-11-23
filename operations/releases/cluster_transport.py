"""Short-lived runner HTTP adapter; the existing SSA executor remains the writer."""
from __future__ import annotations

import contextlib
import http.server
import json
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from http_stream import copy_response

MAIN_API = "https://kube-api.service.re8ch.com"
CLOUD_PROXY = "/api/v1/namespaces/ai-operations/services/re8ch-cloud-platform:19443/proxy/k8s/"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def no_redirect_open(request, *, timeout=120):
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)


@contextlib.contextmanager
def transport(cluster_ref, binding_digest, token_source, *, release_id, open_url=no_redirect_open):
    """Only this command's loopback socket exists; proofs never enter argv/files."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def forward(self):
            target = urllib.parse.urlsplit(self.path)
            if target.scheme or target.netloc or not target.path.startswith("/"):
                self.send_error(400)
                return
            token = token_source()
            if not token:
                self.send_error(401)
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else None
            headers = {key: self.headers[key] for key in ("Content-Type", "Accept", "Accept-Encoding") if self.headers.get(key)}
            headers.update({"Authorization": "Bearer " + token, "X-Re8ch-Execution-Proof": "Bearer " + token,
                            "X-Re8ch-Release-Id": release_id,
                            "X-Re8ch-Cluster-Binding": binding_digest})
            request = urllib.request.Request(MAIN_API + CLOUD_PROXY + urllib.parse.quote(cluster_ref, safe="") + self.path,
                                             data=body, headers=headers, method=self.command)
            try:
                response = open_url(request, timeout=120)
            except urllib.error.HTTPError as error:
                response = error
            except Exception:
                self.send_error(502, "managed cluster request failed")
                return
            with response:
                self.send_response(response.status)
                for key in ("Content-Type", "Content-Encoding", "Warning", "Audit-Id"):
                    for value in response.headers.get_all(key, []):
                        self.send_header(key, value)
                self.send_header("Connection", "close")
                self.end_headers()
                copy_response(response, self.connection, self.wfile)
            self.close_connection = True

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = forward

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="re8ch-target-transport-") as directory:
            config = Path(directory) / "kubeconfig"
            config.write_text(json.dumps({"apiVersion": "v1", "kind": "Config",
                "clusters": [{"name": cluster_ref, "cluster": {"server": f"http://127.0.0.1:{server.server_port}"}}],
                "users": [{"name": "request-local", "user": {}}],
                "contexts": [{"name": cluster_ref, "context": {"cluster": cluster_ref, "user": "request-local"}}],
                "current-context": cluster_ref}))
            config.chmod(0o600)
            yield str(config)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
