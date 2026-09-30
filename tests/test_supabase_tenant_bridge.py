"""The tenant publication must derive Supabase access from live identity state."""

import base64
import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest


COMPONENT = Path(__file__).resolve().parents[1] / "clusters/re8ch/components/ai-operations"


def load(name, filename):
    sys.path.insert(0, str(COMPONENT))
    try:
        spec = importlib.util.spec_from_file_location(name, COMPONENT / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def jwt(issuer, audience, subject="11111111-1111-4111-8111-111111111111"):
    payload = json.dumps({"iss": issuer, "aud": audience, "sub": subject}).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    return "header." + encoded + ".signature"


def test_gotrue_user_endpoint_validates_subject_and_audience(monkeypatch):
    mcp = load("tenant_supabase_mcp", "ai_operations_mcp.py")
    token = jwt(mcp.SUPABASE_ISSUER, "authenticated")
    calls = []

    def userinfo(request, timeout):
        calls.append((request.full_url, request.get_header("Authorization"), timeout))
        return Response(json.dumps({"id": "11111111-1111-4111-8111-111111111111", "email": "test@example.invalid"}).encode())

    monkeypatch.setattr(mcp.urllib.request, "urlopen", userinfo)
    identity = mcp.supabase_identity(token)
    assert identity["auth_type"] == "supabase" and identity["subject"] == "11111111-1111-4111-8111-111111111111"
    assert calls == [(mcp.SUPABASE_USER_URL, "Bearer " + token, 5)]
    assert mcp.supabase_identity(jwt(mcp.SUPABASE_ISSUER, "re8ch-tenant")) is None
    assert mcp.supabase_identity(jwt("https://forged.example", "authenticated")) is None
    assert len(calls) == 1
    monkeypatch.setattr(mcp.urllib.request, "urlopen", lambda *_args, **_kwargs: Response(b'{"id":"another-user"}'))
    assert mcp.supabase_identity(token) is None
    monkeypatch.setattr(mcp.urllib.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(urllib.error.HTTPError("", 401, "", {}, None)))
    assert mcp.supabase_identity(token) is None


def test_supabase_membership_controls_tenant_role_and_revocation(monkeypatch):
    controller = load("tenant_supabase_controller", "tenant_control_controller.py")

    class Kube:
        def list(self, plural):
            assert plural == "tenants"
            return [{"metadata": {"name": "tenant-ci"}, "spec": {"lifecycle": "Active", "namespacePolicy": {"defaultNamespace": "tenant-ci-default"}}}]

    monkeypatch.setattr(controller, "KUBE", Kube())
    rows = [{"tenant_ref": "tenant-ci", "tenant_role": "operator"}]

    def memberships(request, timeout):
        assert request.full_url.endswith("/rpc/my_tenant_runtime_memberships")
        assert request.get_header("Authorization") == "Bearer valid-token"
        assert timeout == 5
        return Response(json.dumps(rows).encode())

    monkeypatch.setattr(controller.urllib.request, "urlopen", memberships)
    assert controller.resolve_supabase_membership("valid-token", "tenant-ci") == {
        "tenantRef": "tenant-ci", "role": "operator", "defaultNamespace": "tenant-ci-default", "runtimeNamespaceRef": "tenant-ci-default",
    }
    rows.append({"tenant_ref": "personal-example", "tenant_role": "admin"})
    assert controller.resolve_supabase_membership("valid-token", "")["tenantRef"] == "tenant-ci"
    rows.append({"tenant_ref": "another-team", "tenant_role": "viewer"})
    with pytest.raises(PermissionError, match="select an active tenant"):
        controller.resolve_supabase_membership("valid-token", "")
    rows.clear()
    with pytest.raises(PermissionError, match="not an active member"):
        controller.resolve_supabase_membership("valid-token", "tenant-ci")
    rows.append({"tenant_ref": "tenant-ci", "tenant_role": "admin"})
    assert controller.resolve_supabase_membership("valid-token", "tenant-ci")["role"] == "admin"
    with pytest.raises(PermissionError, match="ordinary tenant"):
        controller.resolve_supabase_membership("valid-token", "admin")


def test_tenant_runtime_passes_token_only_to_internal_authorizer(monkeypatch):
    mcp = load("tenant_supabase_context", "ai_operations_mcp.py")
    monkeypatch.setattr(mcp, "PROFILE", "tenant")
    monkeypatch.setattr(mcp, "TENANT_CONTROL_URL", "http://tenant-control-controller")
    monkeypatch.setattr(mcp, "TENANT_CLAIM_SERVICE_TOKEN", "internal-service-token")

    def authorizer(request, timeout):
        assert request.full_url == "http://tenant-control-controller/v1/resolve-supabase"
        assert request.get_header("X-tenant-service-token") == "internal-service-token"
        assert json.loads(request.data) in ({"tenantRef": "tenant-ci", "accessToken": "verified-user-token"},
                                            {"tenantRef": "", "accessToken": "verified-user-token"})
        assert timeout == 8
        return Response(b'{"tenantRef":"tenant-ci","role":"operator","defaultNamespace":"tenant-ci-default","runtimeNamespaceRef":"tenant-ci-default"}')

    monkeypatch.setattr(mcp.urllib.request, "urlopen", authorizer)
    identity = {"auth_type": "supabase", "subject": "user-id", "requested_tenant": "tenant-ci", "_supabase_access_token": "verified-user-token"}
    result = mcp.authenticated_tenant_context(identity)
    assert result == {"tenantRef": "tenant-ci", "role": "operator", "defaultNamespace": "tenant-ci-default", "runtimeNamespaceRef": "tenant-ci-default"}
    assert "verified-user-token" not in json.dumps(result)
    assert mcp.authenticated_tenant_context({**identity, "requested_tenant": ""})["tenantRef"] == "tenant-ci"


def test_internal_supabase_resolver_requires_tenant_service_identity(monkeypatch):
    controller = load("tenant_supabase_http", "tenant_control_controller.py")
    monkeypatch.setattr(controller, "CLAIM_SERVICE_TOKEN", "service-only")
    monkeypatch.setattr(controller, "resolve_supabase_membership", lambda token, tenant: {"tenantRef": tenant, "role": "viewer"} if token == "user-token" else None)

    class Request:
        path = "/v1/resolve-supabase"
        headers = {"X-Tenant-Caller": "tenant", "X-Tenant-Service-Token": "wrong", "Content-Length": "54"}
        rfile = io.BytesIO(b'{"accessToken":"user-token","tenantRef":"tenant-ci"}')

        def send_json(self, status, value):
            self.response = (status, value)

    request = Request()
    controller.Handler.do_POST(request)
    assert request.response[0] == 401
    request.headers = {**request.headers, "X-Tenant-Service-Token": "service-only"}
    request.rfile = io.BytesIO(b'{"accessToken":"user-token","tenantRef":"tenant-ci"}')
    request.headers["Content-Length"] = str(len(request.rfile.getvalue()))
    controller.Handler.do_POST(request)
    assert request.response == (200, {"tenantRef": "tenant-ci", "role": "viewer"})
    request.headers = {**request.headers, "X-Tenant-Caller": "admin"}
    controller.Handler.do_POST(request)
    assert request.response[0] == 404
