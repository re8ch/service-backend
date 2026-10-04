"""Private Unix-socket kubectl proxy for one bound native request."""
import http.client
import os
import re
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from urllib.parse import urlsplit

from runtime import OperationError

MAX_BODY = 4 * 1024 * 1024


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=25)
        self.socket_path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def request(kubeconfig, method, path, body=None):
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise OperationError("INVALID_ARGUMENT", "Unsupported native HTTP method")
    target = urlsplit(path)
    if target.scheme or target.netloc or target.fragment or not target.path.startswith('/apis/') or any(x in path for x in ('\r', '\n', '..')):
        raise OperationError("INVALID_ARGUMENT", "A bound Kubernetes API path is required")
    if body is not None and (not isinstance(body, bytes) or len(body) > MAX_BODY):
        raise OperationError("INVALID_ARGUMENT", "Native request body must be bounded bytes")
    connection = None
    with tempfile.TemporaryDirectory(prefix="re8ch-http-") as directory:
        os.chmod(directory, 0o700)
        endpoint = os.path.join(directory, "proxy.sock")
        rejected = "|".join(sorted({"GET", "POST", "PUT", "PATCH", "DELETE", "CONNECT", "OPTIONS", "HEAD", "TRACE"} - {method}))
        process = subprocess.Popen([
            "kubectl", "--kubeconfig", kubeconfig, "proxy", "--unix-socket", endpoint,
            "--accept-hosts=^localhost$", "--accept-paths=^" + re.escape(target.path) + "$",
            "--reject-paths=^$", "--reject-methods=^(" + rejected + ")$",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        timer = None
        try:
            deadline = time.monotonic() + 5
            while not os.path.exists(endpoint):
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise OperationError("PLUGIN_HTTP_UNAVAILABLE", "Native HTTP transport could not start", retryable=True)
                time.sleep(0.02)
            request_deadline = time.monotonic() + 30
            def stop_proxy():
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            timer = threading.Timer(30, stop_proxy)
            timer.daemon = True
            timer.start()
            connection = UnixConnection(endpoint)
            headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            body = response.read(MAX_BODY + 1)
            if time.monotonic() >= request_deadline:
                raise OperationError("PLUGIN_HTTP_UNAVAILABLE", "Native HTTP request exceeded its deadline", retryable=True)
            if len(body) > MAX_BODY:
                raise OperationError("PLUGIN_HTTP_TOO_LARGE", "Native response exceeds the acceptance bound")
            # Retain duplicate headers for validation only; none are exported.
            headers = {}
            for key, value in response.getheaders():
                headers.setdefault(key.lower(), []).append(value)
            return {"status": response.status, "headers": headers, "body": body,
                    "elapsed": time.monotonic() - (request_deadline - 30),
                    "httpVersion": "1.0" if response.version == 10 else "1.1",
                    "auditRef": "audit://operations/op_" + uuid.uuid4().hex}
        except (OSError, http.client.HTTPException) as exc:
            raise OperationError("PLUGIN_HTTP_UNAVAILABLE", "Native HTTP transport failed", retryable=True) from exc
        finally:
            if timer:
                timer.cancel()
            if connection:
                connection.close()
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


def get(kubeconfig, path):
    return request(kubeconfig, "GET", path)
