import io
import json
import urllib.error

import pytest

import stalwart_admin as admin
from runtime import Identity, OperationError


class Provider:
    endpoint = "https://mail.example.test"

    def __init__(self):
        self.value = {"id": "a", "@type": "User", "name": "alice", "description": "Before",
                      "quotas": {"maxDiskQuota": 1000, "maxFiles": 4, "maxApiKeys": 2}, "usedDiskQuota": 100,
                      "credentials": [{"secret": "must-not-leak"}], "permissions": {"secret": "must-not-leak"}}
        self.state = "s1"
        self.writes = []
        self.calls = []
        self.fail_after_write = False

    def get(self, object_type, ids):
        self.calls.append((object_type, "get", ids))
        if self.fail_after_write and self.writes:
            raise OperationError("STALWART_PROVIDER_FAILED", "synthetic failure")
        return ([self.value.copy()] if "a" in ids else []), self.state

    def method(self, object_type, operation, args):
        self.calls.append((object_type, operation, args))
        if operation == "query":
            return {"ids": ["a"]}
        assert operation == "set"
        self.writes.append(args)
        if args["ifInState"] != self.state:
            raise OperationError("STALWART_PLAN_STALE", "stale")
        for key, value in args["update"]["a"].items():
            if key.startswith("quotas/"):
                self.value["quotas"][key.split("/")[1]] = value
            else:
                self.value[key] = value
        self.state = "s2"
        return {"updated": {"a": None}}


def preview(provider, patch=None, subject="admin-1"):
    return admin.call("stalwart.admin.plan", {"object": "Account", "id": "a", "patch": patch or {"description": "After"}}, subject, client=provider)


def approve(tmp_path, monkeypatch, plan, **changes):
    record = {"plan": plan["plan"], "approvedRevision": "a" * 40, "expiresAt": 4102444800}
    record.update(changes)
    (tmp_path / (plan["planHash"] + ".json")).write_text(json.dumps(record))
    monkeypatch.setenv("STALWART_APPROVED_PLANS_DIR", str(tmp_path))


def test_read_has_bounded_query_and_allowlisted_metadata():
    provider = Provider()
    result = admin.call("stalwart.admin.read", {"object": "Account", "limit": 1}, "admin-1", client=provider)
    assert result["nextPosition"] == 1
    assert provider.calls[0] == ("Account", "query", {"position": 0, "limit": 1, "calculateTotal": False})
    encoded = json.dumps(result)
    assert "must-not-leak" not in encoded
    assert "credentials" not in encoded
    assert "permissions" not in encoded
    assert "maxApiKeys" not in encoded


@pytest.mark.parametrize("arguments", [
    {"object": "ApiKey"}, {"object": "Account", "id": "../secret"},
    {"object": "Account", "limit": 51}, {"object": "Account", "limit": True},
    {"object": "Account", "origin": "https://evil.test"},
])
def test_invalid_read_never_reaches_provider(arguments):
    provider = Provider()
    with pytest.raises(OperationError) as failure:
        admin.call("stalwart.admin.read", arguments, "admin-1", client=provider)
    assert failure.value.code == "INVALID_ARGUMENT"
    assert provider.calls == []


@pytest.mark.parametrize("patch", [
    {"credentials": []}, {"roles": {"@type": "Admin"}}, {"description/other": "value"},
    {"quotas": {"maxApiKeys": 500}}, {"quotas": {"maxFiles": 0}},
    {"quotas": {"maxDiskQuota": 1 << 41}}, {"quotas": {"maxFiles": True}}, {},
])
def test_patch_cannot_change_privileges_or_unbound_fields(patch):
    provider = Provider()
    with pytest.raises(OperationError):
        admin.call("stalwart.admin.plan", {"object": "Account", "id": "a", "patch": patch}, "admin-1", client=provider)
    assert provider.writes == []


def test_plan_preserves_unrequested_quotas_and_requires_review():
    provider = Provider()
    plan = preview(provider, {"quotas": {"maxFiles": 8}})
    assert plan["decision"] == "awaiting_gitops_review"
    assert plan["nativeValidation"] == "deferred_to_provider_on_apply"
    assert plan["plan"]["nativePatch"] == {"quotas/maxFiles": 8}
    assert plan["planHash"] == admin.digest(plan["plan"])
    assert "must-not-leak" not in json.dumps(plan)
    assert not provider.writes


def test_quota_shrink_below_usage_is_rejected():
    with pytest.raises(OperationError) as failure:
        preview(Provider(), {"quotas": {"maxDiskQuota": 99}})
    assert failure.value.code == "STALWART_QUOTA_BELOW_USAGE"


def test_domain_does_not_accept_account_quota():
    with pytest.raises(OperationError):
        admin.call("stalwart.admin.plan", {"object": "Domain", "id": "a", "patch": {"quotas": {"maxFiles": 2}}}, "admin-1", client=Provider())


def test_apply_without_installed_approval_is_denied(monkeypatch):
    monkeypatch.delenv("STALWART_APPROVED_PLANS_DIR", raising=False)
    provider = Provider()
    plan = preview(provider)
    with pytest.raises(OperationError) as failure:
        admin.call("stalwart.admin.apply", {"planHash": plan["planHash"]}, "admin-1", client=provider)
    assert failure.value.code == "STALWART_APPROVAL_REQUIRED"
    assert not provider.writes


@pytest.mark.parametrize("change", ["expired", "wrong_subject", "wrong_origin", "tampered", "invalid_revision"])
def test_approval_binds_exact_plan_subject_origin_and_expiry(tmp_path, monkeypatch, change):
    provider = Provider()
    plan = preview(provider)
    changes = {}
    if change == "expired":
        changes["expiresAt"] = 1
    elif change == "invalid_revision":
        changes["approvedRevision"] = "main"
    elif change == "tampered":
        plan["plan"]["patch"]["description"] = "tampered"
    approve(tmp_path, monkeypatch, plan, **changes)
    if change == "wrong_origin":
        provider.endpoint = "https://different.example.test"
    with pytest.raises(OperationError):
        admin.call("stalwart.admin.apply", {"planHash": plan["planHash"]}, "other-admin" if change == "wrong_subject" else "admin-1", client=provider)
    assert not provider.writes


def test_apply_rechecks_native_state_and_verifies_result(tmp_path, monkeypatch):
    provider = Provider()
    plan = preview(provider, {"quotas": {"maxFiles": 8}})
    approve(tmp_path, monkeypatch, plan)
    result = admin.call("stalwart.admin.apply", {"planHash": plan["planHash"]}, "admin-1", client=provider)
    assert result["status"] == "applied" and result["verified"]
    assert provider.writes == [{"ifInState": "s1", "update": {"a": {"quotas/maxFiles": 8}}}]
    assert provider.value["quotas"]["maxApiKeys"] == 2
    replay = admin.call("stalwart.admin.apply", {"planHash": plan["planHash"]}, "admin-1", client=provider)
    assert replay["status"] == "already_satisfied"
    assert len(provider.writes) == 1


def test_apply_stale_plan_does_not_write(tmp_path, monkeypatch):
    provider = Provider()
    plan = preview(provider)
    approve(tmp_path, monkeypatch, plan)
    provider.state = "changed-by-another-admin"
    with pytest.raises(OperationError) as failure:
        admin.call("stalwart.admin.apply", {"planHash": plan["planHash"]}, "admin-1", client=provider)
    assert failure.value.code == "STALWART_PLAN_STALE"
    assert not provider.writes


def test_unknown_write_outcome_is_not_replayed(tmp_path, monkeypatch):
    provider = Provider()
    plan = preview(provider)
    approve(tmp_path, monkeypatch, plan)
    provider.fail_after_write = True
    result = admin.call("stalwart.admin.apply", {"planHash": plan["planHash"]}, "admin-1", client=provider)
    assert result["status"] == "outcome_unknown" and result["applied"] is None
    assert len(provider.writes) == 1


class Response(io.BytesIO):
    status = 200


class Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return Response(response if isinstance(response, bytes) else json.dumps(response).encode())


def test_native_client_uses_scoped_properties_and_same_origin():
    transport = Transport([
        {"capabilities": {admin.CAPABILITY: {}}, "apiUrl": "https://mail.example.test/jmap/"},
        {"methodResponses": [["x:Account/get", {"list": [{"id": "a"}], "state": "s1"}, "admin"]]},
    ])
    client = admin.Client("https://mail.example.test", "test-only", opener=transport)
    assert client.get("Account", ["a"])[1] == "s1"
    payload = json.loads(transport.calls[1].data)
    assert payload["methodCalls"][0][0] == "x:Account/get"
    assert "credentials" not in payload["methodCalls"][0][1]["properties"]
    assert transport.calls[1].headers["Authorization"] == "Bearer test-only"


@pytest.mark.parametrize("api_url", ["https://evil.test/jmap/", "http://mail.example.test/jmap/", "https://mail.example.test/api/keys", "https://mail.example.test/jmap/?secret=value"])
def test_discovered_endpoint_cannot_redirect_credentials(api_url):
    transport = Transport([{"capabilities": {admin.CAPABILITY: {}}, "apiUrl": api_url}])
    with pytest.raises(OperationError) as failure:
        admin.Client("https://mail.example.test", "test-only", opener=transport).get("Account", ["a"])
    assert failure.value.code == "STALWART_ENDPOINT_REJECTED"
    assert len(transport.calls) == 1
    assert admin.NoRedirect().redirect_request(None, None, 302, None, None, "https://evil.test") is None


def test_unsupported_server_never_attempts_management():
    transport = Transport([{"capabilities": {}, "apiUrl": "https://mail.example.test/jmap/"}])
    with pytest.raises(OperationError) as failure:
        admin.Client("https://mail.example.test", "test-only", opener=transport).get("Account", ["a"])
    assert failure.value.code == "STALWART_UNSUPPORTED"
    assert len(transport.calls) == 1


def test_provider_error_and_oversized_content_do_not_leak():
    for response, expected in [
        (urllib.error.HTTPError("https://mail.example.test", 401, "must-not-leak", {}, None), "STALWART_PROVIDER_FAILED"),
        (b"x" * (admin.MAX_RESPONSE + 1), "STALWART_RESPONSE_LIMIT"),
    ]:
        transport = Transport([response])
        with pytest.raises(OperationError) as failure:
            admin.Client("https://mail.example.test", "test-only", opener=transport).discover()
        assert failure.value.code == expected
        assert "must-not-leak" not in str(failure.value)


def test_telemetry_omits_object_data_and_plain_subject(caplog):
    result = preview(Provider(), {"description": "private-description"}, subject="private-subject")
    assert result["operationId"].startswith("op_")
    assert "private-description" not in caplog.text
    assert "private-subject" not in caplog.text
    assert "must-not-leak" not in caplog.text
    assert result["planHash"] in caplog.text


def test_bad_query_response_has_a_fixed_error():
    provider = Provider()
    provider.method = lambda *args: {"ids": [{"secret": "must-not-leak"}]}
    with pytest.raises(OperationError) as failure:
        admin.call("stalwart.admin.read", {"object": "Account"}, "admin-1", client=provider)
    assert failure.value.code == "STALWART_RESPONSE_INVALID"
    assert "must-not-leak" not in str(failure.value)


def test_native_state_race_is_not_replayed(tmp_path, monkeypatch):
    provider = Provider()
    plan = preview(provider)
    approve(tmp_path, monkeypatch, plan)
    original = provider.method

    def racing_method(object_type, operation, args):
        if operation == "set":
            provider.state = "raced"
        return original(object_type, operation, args)

    provider.method = racing_method
    with pytest.raises(OperationError) as failure:
        admin.call("stalwart.admin.apply", {"planHash": plan["planHash"]}, "admin-1", client=provider)
    assert failure.value.code == "STALWART_PLAN_STALE"
    assert len(provider.writes) == 1
    assert provider.value["description"] == "Before"


def test_non_admin_cannot_discover_or_invoke_management_tools():
    from server import ADMIN_GROUP, dispatch, tool_list
    normal = Identity("member", ())
    assert not ({t["name"] for t in tool_list(normal)} & set(admin.BY_NAME))
    for name in admin.BY_NAME:
        with pytest.raises(OperationError) as failure:
            dispatch(None, name, {}, normal)
        assert failure.value.code == "AUTHORIZATION_DENIED"
    privileged = Identity("admin-1", (ADMIN_GROUP,))
    assert set(admin.BY_NAME) <= {t["name"] for t in tool_list(privileged)}
