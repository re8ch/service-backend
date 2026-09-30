#!/usr/bin/env python3
"""Upload the ten oldest Codex files to a new DataSpace holding folder."""

import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path


ISSUER = "https://id.re8ch.com"
GATEWAY = "https://storage.service.re8ch.com"
CLIENT_ID = "storage-collaborator"
REDIRECT = "http://127.0.0.1:8765/callback"
SOURCE = Path.home() / "Documents" / "Codex"


def candidates(root: Path) -> list[Path]:
    found = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name != ".git" and not name.startswith(".dataspace-")]
        found.extend(Path(directory) / name for name in names if name != ".DS_Store")
    return sorted(found)[:10]


def request(url: str, token: str = "", method: str = "GET", body: bytes | None = None,
            headers: dict[str, str] | None = None) -> tuple[bytes, dict]:
    h = dict(headers or {})
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read(), dict(response.headers)


def login() -> str:
    discovery = json.loads(request(ISSUER + "/.well-known/openid-configuration")[0])
    if discovery["issuer"] != ISSUER:
        raise RuntimeError("unexpected OIDC issuer")
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    received: dict[str, str] = {}
    event = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            values = urllib.parse.parse_qs(parsed.query)
            if parsed.path != "/callback" or values.get("state", [""])[0] != state:
                self.send_error(400)
                return
            received["code"] = values.get("code", [""])[0]
            received["error"] = values.get("error", [""])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"DataSpace login received. Return to the terminal.")
            event.set()

        def log_message(self, *_args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 8765), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    params = urllib.parse.urlencode({
        "client_id": CLIENT_ID, "redirect_uri": REDIRECT, "response_type": "code",
        "scope": "openid offline_access", "code_challenge": challenge,
        "code_challenge_method": "S256", "state": state,
    })
    print("Opening browser for DataSpace login...", flush=True)
    webbrowser.open(discovery["authorization_endpoint"] + "?" + params)
    event.wait(180)
    server.shutdown()
    server.server_close()
    if received.get("error") or not received.get("code"):
        raise RuntimeError("OIDC login did not complete")
    form = urllib.parse.urlencode({
        "grant_type": "authorization_code", "code": received["code"],
        "redirect_uri": REDIRECT, "client_id": CLIENT_ID, "code_verifier": verifier,
    }).encode()
    result = json.loads(request(discovery["token_endpoint"], method="POST", body=form,
                                headers={"Content-Type": "application/x-www-form-urlencoded"})[0])
    return result["id_token"]


def api(token: str, path: str, method: str = "GET", body: bytes | None = None,
        headers: dict[str, str] | None = None) -> dict:
    data, _ = request(GATEWAY + path, token, method, body, headers)
    return json.loads(data) if data else {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args()
    root = args.source.resolve()
    files = candidates(root)
    if len(files) != 10:
        raise RuntimeError(f"expected ten source files, found {len(files)}")
    entries = [{"source": str(p.relative_to(root)), "size": p.stat().st_size,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]
    if args.list_only:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return

    request(GATEWAY + "/healthz")
    token = login()
    workspace = api(token, "/v1/workspaces")["workspaces"][0]
    root_id = workspace["rootItemId"]
    holding_name = "codex-unassigned-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    holding = api(token, f"/v1/items/{root_id}", "POST",
                  json.dumps({"name": holding_name, "kind": "folder"}).encode(),
                  {"Content-Type": "application/json"})
    report = {"workspaceId": workspace["id"], "folderId": holding["id"],
              "folderName": holding_name, "gateway": GATEWAY, "items": []}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    for index, (path, entry) in enumerate(zip(files, entries), 1):
        remote_name = f"{index:02d}-{path.name}"
        item = api(token, f"/v1/items/{holding['id']}", "POST",
                   json.dumps({"name": remote_name, "kind": "file"}).encode(),
                   {"Content-Type": "application/json"})
        uploaded = api(token, f"/v1/items/{item['id']}", "PATCH", path.read_bytes(),
                       {"Content-Type": "application/octet-stream", "If-Match": item["etag"]})
        downloaded, _ = request(GATEWAY + f"/v1/items/{item['id']}/content", token)
        digest = hashlib.sha256(downloaded).hexdigest()
        if digest != entry["sha256"] or len(downloaded) != entry["size"]:
            raise RuntimeError(f"download verification failed for {entry['source']}")
        report["items"].append({**entry, "itemId": item["id"], "remoteName": remote_name,
                                "version": uploaded["version"]})
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"Verified {index}/10: {entry['source']}", flush=True)
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
