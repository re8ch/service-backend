import http.server
import threading
import urllib.error
import urllib.request
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "operations/releases"))
from cluster_bindings import no_redirect_open as cloud_open
from cluster_transport import no_redirect_open as runner_open


@pytest.mark.parametrize("open_url", [cloud_open, runner_open])
def test_cross_host_redirect_cannot_receive_caller_proof(open_url):
    stolen = []
    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            stolen.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()
        def log_message(self, *_): pass
    target = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Target)
    thread = threading.Thread(target=target.serve_forever, daemon=True)
    thread.start()
    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/steal")
            self.end_headers()
        def log_message(self, *_): pass
    source = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    source_thread = threading.Thread(target=source.serve_forever, daemon=True)
    source_thread.start()
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{source.server_port}/version",
            headers={"Authorization": "Bearer synthetic", "X-Re8ch-Execution-Proof": "Bearer synthetic"})
        with pytest.raises(urllib.error.HTTPError) as error:
            open_url(request, timeout=2)
        assert error.value.code == 302
        error.value.close()
        assert stolen == []
    finally:
        source.shutdown(); source.server_close(); source_thread.join()
        target.shutdown(); target.server_close(); thread.join()
