import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "operations/tenant-provider-acceptance/run.py"
SPEC = importlib.util.spec_from_file_location("tenant_provider_acceptance", SCRIPT)
acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acceptance)


def test_identity_config_requires_exact_user_visible_tenant_phase(monkeypatch):
    calls = []

    def fake_request(url, **kwargs):
        calls.append((url, kwargs))
        return 200, {}, [
            {"tenant_ref": "other", "service_class": "registry.harbor.v1", "instance_name": "test", "observed_phase": "Ready"},
            {"tenant_ref": "acme", "service_class": "registry.harbor.v1", "instance_name": "test", "observed_phase": "Ready"},
        ]

    monkeypatch.setattr(acceptance, "request", fake_request)
    result = acceptance.require_identity_config("https://identity", "user-jwt", "acme", "registry.harbor.v1", "test", "Ready")
    assert result["tenant_ref"] == "acme"
    assert calls == [("https://identity/rpc/my_tenant_service_configs", {"method": "POST", "payload": {}, "headers": {"Authorization": "Bearer user-jwt"}})]
    with pytest.raises(acceptance.AcceptanceError, match="missing"):
        acceptance.require_identity_config("https://identity", "user-jwt", "acme", "registry.harbor.v1", "test", "Deleted")


def test_identity_config_rejects_private_fields(monkeypatch):
    monkeypatch.setattr(acceptance, "request", lambda *_args, **_kwargs: (
        200, {}, [{"tenant_ref": "acme", "service_class": "observability.grafana.v1", "instance_name": "test", "observed_phase": "Ready", "credential_ref": "private"}]
    ))
    with pytest.raises(acceptance.AcceptanceError, match="private identity"):
        acceptance.require_identity_config("https://identity", "user-jwt", "acme", "observability.grafana.v1", "test", "Ready")


def test_grafana_web_login_uses_a_session_cookie():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == "/grafana/login"
            assert json.loads(self.rfile.read(int(self.headers["Content-Length"]))) == {"user": "acme-admin", "password": "test-secret"}
            self.send_response(200)
            self.send_header("Set-Cookie", "grafana_session=test-session; Path=/grafana; HttpOnly")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def do_GET(self):
            assert self.path == "/grafana/api/user/orgs"
            assert "grafana_session=test-session" in self.headers.get("Cookie", "")
            body = b'[{"orgId":4}]'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        acceptance.grafana_web_login(f"http://127.0.0.1:{server.server_port}/grafana", "acme-admin", "test-secret", 4)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_harbor_cli_uses_isolated_config_and_password_stdin(monkeypatch):
    calls = []
    monkeypatch.setattr(acceptance.shutil, "which", lambda name: "/usr/bin/docker")

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="abc123\n" if "inspect" in argv else "", stderr="")

    monkeypatch.setattr(acceptance.subprocess, "run", fake_run)
    ref = acceptance.harbor_cli_round_trip("https://registry.example.test", "acme", "acme-user", "private-password", "abc123")
    assert ref == "registry.example.test/acme/tenant-acceptance:abc123"
    assert [argv[1] for argv, _ in calls[:6]] == ["login", "build", "push", "image", "pull", "image"]
    assert calls[4][0][2:4] == ["--platform", "linux/amd64"]
    assert calls[0][1]["input"] == "private-password\n"
    assert all("private-password" not in " ".join(argv) for argv, _ in calls)
    assert len({kwargs["env"]["DOCKER_CONFIG"] for _, kwargs in calls}) == 1


def test_authoritative_route_check_rejects_one_unhealthy_nameserver(monkeypatch):
    monkeypatch.setattr(acceptance.shutil, "which", lambda _name: "/usr/bin/tool")

    def fake_run(argv, **_kwargs):
        if argv[:2] == ["dig", "+short"]:
            if argv[2:] == ["NS", "service.re8ch.com"]:
                return SimpleNamespace(returncode=0, stdout="ns-good.\nns-bad.\n")
            address = "192.0.2.146" if argv[2] == "@ns-good" else "192.0.2.111"
            return SimpleNamespace(returncode=0, stdout=address + "\n")
        assert argv[0] == "curl"
        healthy = any("192.0.2.146" in value for value in argv)
        return SimpleNamespace(returncode=0 if healthy else 7, stdout="401" if healthy else "000")

    monkeypatch.setattr(acceptance.subprocess, "run", fake_run)
    with pytest.raises(acceptance.AcceptanceError, match="ns-bad directs tools.service.re8ch.com to unavailable 192.0.2.111"):
        acceptance.authoritative_route_preflight("tools.service.re8ch.com", "/tenant/mcp", 401)
