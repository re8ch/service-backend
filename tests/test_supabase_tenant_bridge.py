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
    rows = [{"tenant_ref": "tenant-ci", "tenant_role": "operator", "tenant_kind": "organization"}]

    def memberships(request, timeout):
        assert request.full_url.endswith("/rpc/my_tenant_runtime_contexts")
        assert request.get_header("Authorization") == "Bearer valid-token"
        assert timeout == 5
        return Response(json.dumps(rows).encode())

    monkeypatch.setattr(controller.urllib.request, "urlopen", memberships)
    assert controller.resolve_supabase_membership("valid-token", "tenant-ci") == {
        "tenantRef": "tenant-ci", "runtimeTenantRef": "tenant-ci", "role": "operator",
        "defaultNamespace": "tenant-ci-default", "runtimeNamespaceRef": "tenant-ci-default",
    }
    rows.append({"tenant_ref": "personal-example", "tenant_role": "admin", "tenant_kind": "personal"})
    assert controller.resolve_supabase_membership("valid-token", "")["tenantRef"] == "tenant-ci"
    rows.append({"tenant_ref": "another-team", "tenant_role": "viewer", "tenant_kind": "organization"})
    with pytest.raises(PermissionError, match="select an active tenant"):
        controller.resolve_supabase_membership("valid-token", "")
    rows.clear()
    with pytest.raises(PermissionError, match="not an active member"):
        controller.resolve_supabase_membership("valid-token", "tenant-ci")
    rows.append({"tenant_ref": "tenant-ci", "tenant_role": "admin", "tenant_kind": "organization"})
    assert controller.resolve_supabase_membership("valid-token", "tenant-ci")["role"] == "admin"
    with pytest.raises(PermissionError, match="ordinary tenant"):
        controller.resolve_supabase_membership("valid-token", "admin")


def test_new_personal_organizations_share_runtime_class_without_sharing_owner(monkeypatch):
    controller = load("tenant_shared_personal", "tenant_control_controller.py")

    class Kube:
        created = []

        def list(self, plural):
            if plural == "tenantidentitybindings":
                return []
            assert plural == "tenants"
            return [{"metadata": {"name": "personal-standard", "uid": "shared-class-uid"},
                     "spec": {"lifecycle": "Active", "namespacePolicy": {
                         "defaultNamespace": "personal-standard-default", "maxNamespaces": 10,
                         "allowCreate": True, "allowDelete": True}}}]

        def create(self, plural, resource):
            assert plural == "tenantnamespaces"
            self.created.append(resource)
            return resource

    cluster = Kube()
    monkeypatch.setattr(controller, "KUBE", cluster)
    owner = {"tenant_ref": "personal-11111111-1111-4111-8111-111111111111",
             "tenant_role": "admin", "tenant_kind": "personal"}
    other = {"tenant_ref": "personal-22222222-2222-4222-8222-222222222222",
             "tenant_role": "admin", "tenant_kind": "personal"}
    rows = [owner]
    monkeypatch.setattr(controller.urllib.request, "urlopen", lambda *_args, **_kwargs: Response(json.dumps(rows).encode()))
    first = controller.resolve_supabase_membership("valid-token", "")
    assert first["tenantRef"] == owner["tenant_ref"]
    assert first["runtimeTenantRef"] == "personal-standard"
    assert first["defaultNamespace"] == owner["tenant_ref"] + "-default"
    assert cluster.created[0]["spec"]["tenantRef"] == owner["tenant_ref"]
    assert cluster.created[0]["spec"]["namespaceName"] == first["defaultNamespace"]
    assert controller.resolve_supabase_membership("valid-token", "")["tenantRef"] == owner["tenant_ref"]
    assert len(cluster.created) == 1
    virtual = controller.tenant_definition(owner["tenant_ref"])
    assert virtual["metadata"]["name"] == owner["tenant_ref"]
    assert virtual["metadata"]["uid"] == "shared-class-uid"
    assert virtual["spec"]["namespacePolicy"]["allowedPrefixes"] == [owner["tenant_ref"] + "-"]
    assert controller.harbor_project(owner["tenant_ref"]) == owner["tenant_ref"]
    assert controller.runtime_namespace(owner["tenant_ref"]) == first["defaultNamespace"]
    assert controller.tenant_namespace_resources(virtual, first["defaultNamespace"])[0]["metadata"]["labels"]["tenancy.re8ch.com/tenant"] == owner["tenant_ref"]
    with pytest.raises(PermissionError, match="not an active member"):
        controller.resolve_supabase_membership("valid-token", other["tenant_ref"])
    rows[:] = [other]
    second = controller.resolve_supabase_membership("valid-token", "")
    assert second["tenantRef"] == other["tenant_ref"]
    assert second["runtimeTenantRef"] == first["runtimeTenantRef"]
    assert second["defaultNamespace"] != first["defaultNamespace"]
    assert cluster.created[1]["spec"]["tenantRef"] == other["tenant_ref"]
    with pytest.raises(PermissionError):
        controller.validate_tenant_namespace(owner["tenant_ref"], second["defaultNamespace"], "EnsurePresent")
    rows.clear()
    with pytest.raises(PermissionError, match="select an active tenant"):
        controller.resolve_supabase_membership("valid-token", "")


def test_shared_personal_namespace_reconciles_with_its_own_owner(monkeypatch):
    controller = load("tenant_personal_namespace", "tenant_control_controller.py")
    owner = "personal-11111111-1111-4111-8111-111111111111"
    other = "personal-22222222-2222-4222-8222-222222222222"
    runtime = {"metadata": {"name": "personal-standard", "uid": "class-uid"},
               "spec": {"lifecycle": "Active", "namespacePolicy": {"defaultNamespace": "personal-standard-default",
                        "allowedPrefixes": ["personal-standard-"], "allowCreate": True, "allowDelete": True}}}

    class Kube:
        namespaces = {}
        status = {}

        def list(self, plural):
            if plural == "tenants":
                return [runtime]
            assert plural == "tenantidentitybindings"
            return []

        def get(self, plural, name, api=None):
            assert plural == "namespaces"
            if name not in self.namespaces:
                raise controller.urllib.error.HTTPError("", 404, "missing", {}, None)
            return self.namespaces[name]

        def apply(self, resource):
            if resource["kind"] == "Namespace":
                name = resource["metadata"]["name"]
                self.namespaces[name] = {"metadata": {**resource["metadata"], "uid": name + "-uid"}}

        def patch_status(self, plural, name, status):
            assert plural == "tenantnamespaces"
            self.status[name] = status

    cluster = Kube()
    monkeypatch.setattr(controller, "KUBE", cluster)
    for personal in (owner, other):
        namespace = personal + "-default"
        item = {"metadata": {"name": controller.tenant_namespace_name(personal, namespace), "uid": personal, "generation": 1},
                "spec": {"tenantRef": personal, "namespaceName": namespace, "operation": "EnsurePresent"}}
        controller.reconcile_tenant_namespace(item, {"personal-standard": runtime})
        assert cluster.namespaces[namespace]["metadata"]["labels"]["tenancy.re8ch.com/tenant"] == personal
        assert cluster.status[item["metadata"]["name"]]["phase"] == "Ready"
    assert cluster.namespaces[owner + "-default"]["metadata"]["labels"] != cluster.namespaces[other + "-default"]["metadata"]["labels"]
    with pytest.raises(PermissionError, match="outside the authenticated tenant prefix"):
        controller.reconcile_tenant_namespace({"metadata": {"name": "invalid", "uid": "invalid"},
                                               "spec": {"tenantRef": owner, "namespaceName": other + "-default", "operation": "EnsurePresent"}},
                                              {"personal-standard": runtime})


def test_suspended_dedicated_personal_tenant_cannot_fall_back_to_shared_class(monkeypatch):
    controller = load("tenant_suspended_personal", "tenant_control_controller.py")
    owner = "personal-11111111-1111-4111-8111-111111111111"

    class Kube:
        def list(self, plural):
            assert plural == "tenants"
            return [{"metadata": {"name": owner}, "spec": {"lifecycle": "Suspended"}},
                    {"metadata": {"name": "personal-standard"}, "spec": {"lifecycle": "Active"}}]

    monkeypatch.setattr(controller, "KUBE", Kube())
    rows = [{"tenant_ref": owner, "tenant_role": "admin", "tenant_kind": "personal"}]
    monkeypatch.setattr(controller.urllib.request, "urlopen", lambda *_args, **_kwargs: Response(json.dumps(rows).encode()))
    with pytest.raises(PermissionError, match="not active"):
        controller.resolve_supabase_membership("valid-token", "")
    with pytest.raises(PermissionError, match="active tenant definition"):
        controller.tenant_definition(owner)


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
