import hashlib
import hmac
import json
import re
import ssl
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).parent
REPOS = json.loads((ROOT / "repositories.json").read_text())
SA = Path("/var/run/secrets/kubernetes.io/serviceaccount")
NAMESPACE = "gitops-events"


def valid(data):
    return (set(data) == {"repository", "repository_id", "release_id", "delivery"}
            and REPOS.get(data["repository_id"]) == data["repository"]
            and re.fullmatch(r"[1-9][0-9]{0,19}", data["release_id"])
            and str(uuid.UUID(data["delivery"])) == data["delivery"])


def persist(data):
    name = "release-" + data["delivery"]
    obj = {"apiVersion": "v1", "kind": "ConfigMap", "immutable": True,
           "metadata": {"name": name, "namespace": NAMESPACE,
                        "labels": {"gitops.re8ch.com/receipt": "release"}}, "data": data}
    url = f"https://kubernetes.default.svc/api/v1/namespaces/{NAMESPACE}/configmaps"
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + (SA / "token").read_text()}
    ctx = ssl.create_default_context(cafile=str(SA / "ca.crt"))
    try:
        with urlopen(Request(url, json.dumps(obj).encode(), headers), context=ctx, timeout=6):
            return 201
    except HTTPError as exc:
        if exc.code != 409:
            raise
    with urlopen(Request(url + "/" + name, headers=headers), context=ctx, timeout=4) as resp:
        return 200 if json.load(resp).get("data") == data else 409


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_error(200 if self.path == "/healthz" else 404)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2_000_000:
                return self.send_error(413)
            self.connection.settimeout(8)
            body = self.rfile.read(length)
            if self.server.admission:
                if self.path.partition("?")[0] != "/validate":
                    return self.send_error(404)
                req = json.loads(body)["request"]
                obj = req["object"]
                try:
                    allowed = bool(valid(obj.get("data", {})) and obj.get("immutable") is True
                                   and obj["metadata"]["name"] == "release-" + obj["data"]["delivery"])
                except (KeyError, TypeError, ValueError):
                    allowed = False
                result = {"apiVersion": "admission.k8s.io/v1", "kind": "AdmissionReview",
                          "response": {"uid": req["uid"], "allowed": allowed}}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())
                return
            if self.path != "/events/github/gitops":
                return self.send_error(404)
            key = Path("/key/secret").read_bytes()
            signature = "sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest()
            if not key or not hmac.compare_digest(signature, self.headers.get("X-Hub-Signature-256", "")):
                return self.send_error(401)
            event = self.headers.get("X-GitHub-Event")
            payload = json.loads(body)
            if event != "release" or payload.get("action") != "published":
                self.send_response(204)
            else:
                data = {"repository": payload["repository"]["full_name"],
                        "repository_id": str(payload["repository"]["id"]),
                        "release_id": str(payload["release"]["id"]),
                        "delivery": self.headers["X-GitHub-Delivery"]}
                self.send_response(persist(data) if valid(data) else 403)
            self.end_headers()
        except (KeyError, ValueError, TypeError):
            self.send_error(400)
        except OSError:
            self.send_error(503)


if __name__ == "__main__":
    admission = sys.argv[1] == "admission"
    server = ThreadingHTTPServer(("", 8443 if admission else 8080), Handler)
    server.admission = admission
    if admission:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain("/tls/tls.crt", "/tls/tls.key")
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
