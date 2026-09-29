import base64
import importlib.util
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "clusters/re8ch/components/ai-operations"
sys.path.insert(0, str(COMPONENT))
SPEC = importlib.util.spec_from_file_location("tenant_provider_reconciler", COMPONENT / "tenant_provider_reconciler.py")
provider = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provider)
sys.path.pop(0)
IDENTITY_USER = "11111111-1111-4111-8111-111111111111"


class FakeIdentity:
    def __init__(self):
        self.records = []

    def human_email(self, tenant, identity_user):
        assert (tenant, identity_user) == ("acme", IDENTITY_USER)
        return "admin@acme.example"

    def record(self, instance, access, phase):
        self.records.append((instance["metadata"]["name"], phase, access))


identity = FakeIdentity()


@pytest.fixture(autouse=True)
def legacy_grafana_environment(monkeypatch):
    # Existing contract cases exercise administrator-provided Grafana bindings.
    # Auto provisioning cases explicitly remove this override.
    monkeypatch.setenv("GRAFANA_TENANT_BINDINGS_JSON", "{}")


def test_identity_store_resolves_membership_and_records_only_opaque_config():
    requests = []

    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return self.body

    def opener(request, timeout):
        requests.append((request.full_url, json.loads(request.data), request.get_header("Authorization")))
        if request.full_url.endswith("resolve_tenant_service_user"):
            return Response(json.dumps([{"user_id": IDENTITY_USER, "email": "admin@acme.example"}]).encode())
        return Response(b"")

    store = provider.IdentityStore("http://identity.invalid", "service-key", opener)
    assert store.human_email("acme", IDENTITY_USER) == "admin@acme.example"
    value = instance("registry.harbor.v1", {"project": "acme", "quotaGiB": 10, "identityUserRef": IDENTITY_USER}, "pull")
    store.record(value, {"project": "acme", "humanLogin": "acme", "humanCredentialRef": "credential://harbor/acme/" + "a" * 32}, "Ready")
    assert requests[1][1]["p_identity_user_id"] == IDENTITY_USER
    assert requests[1][1]["p_credential_ref"].startswith("credential://harbor/acme/")
    assert "humanCredentialRef" not in requests[1][1]["p_observed_config"]
    assert all(header == "Bearer service-key" for _, _, header in requests)


class FakeKube:
    def __init__(self, service_class):
        self.service_class = service_class
        self.secrets = {}
        self.status = None
        self.instances = []
        self.catalog = {"acme": {}}

    def list(self, plural, namespace=None):
        if plural == "tenants":
            return [{"metadata": {"name": "acme"}, "spec": {"lifecycle": "Active", "harborProjectRef": "acme"}}]
        if plural == "tenantgrants":
            return [{"spec": {"tenantRef": "acme", "consumableRef": self.service_class, "roleRef": "operator", "lifecycle": "Active"}}]
        if plural == "tenantserviceinstances":
            return list(self.instances)
        raise AssertionError(plural)

    def get(self, plural, name, namespace=None, api=None):
        if plural == "configmaps":
            return {"data": {"harbor-tenants.json": json.dumps({"tenants": self.catalog})}}
        if plural == "secrets":
            key = (namespace, name)
            if key not in self.secrets:
                raise urllib.error.HTTPError("https://kubernetes.invalid", 404, "not found", {}, None)
            return self.secrets[key]
        raise AssertionError(plural)

    def create(self, plural, value, namespace=None, api=None):
        assert plural == "secrets" and namespace == "credential-broker"
        encoded = {key: base64.b64encode(item.encode()).decode() for key, item in value["stringData"].items()}
        self.secrets[(namespace, value["metadata"]["name"])] = {"metadata": value["metadata"], "data": encoded}
        return value

    def patch_status(self, plural, name, status, namespace):
        assert plural == "tenantserviceinstances" and namespace == "acme"
        self.status = status


class FakeHarbor:
    def __init__(self):
        self.project = None
        self.user = None
        self.created_users = 0
        self.quota = None
        self.members = []
        self.robot_items = []
        self.granted_role_id = None
        self.reset_passwords = []

    def find_project(self, project):
        return self.project

    def create_project(self, project, quota):
        self.project = {"project_id": 7, "name": project, "metadata": {"public": "false"}}

    def ensure_project_quota(self, project_id, quota):
        self.quota = (project_id, quota)

    def find_user(self, username):
        return self.user

    def create_user(self, username, email, password):
        assert password and email == "admin@acme.example"
        self.created_users += 1
        self.user = {"user_id": 17, "username": username, "email": email}

    def reset_user_password(self, user_id, password):
        assert user_id == 17 and password
        self.reset_passwords.append(password)

    def ensure_project_admin(self, project, username):
        assert project == "acme" and username == "acme"
        if not self.members:
            self.members = [{"id": 11, "entity_name": username}]

    def ensure_project_role(self, project, username, role_id):
        self.granted_role_id = role_id
        self.ensure_project_admin(project, username)

    def project_members(self, project):
        return list(self.members)

    def remove_project_member(self, project, member_id):
        self.members = [item for item in self.members if item["id"] != member_id]

    def robots(self):
        return list(self.robot_items)

    def disable_robot(self, robot_id, disabled=True):
        next(item for item in self.robot_items if item["id"] == robot_id)["disable"] = disabled


def instance(service_class, parameters, plan):
    return {"metadata": {"name": "access", "namespace": "acme", "uid": "instance-uid", "generation": 1, "labels": {"tenancy.re8ch.com/tenant": "acme"}}, "spec": {"tenantRef": "acme", "serviceClass": service_class, "plan": plan, "lifecycle": "Requested", "parameters": parameters}, "status": {}}


def test_harbor_reconcile_creates_private_project_quota_and_human_account():
    kube = FakeKube("registry.harbor.v1")
    harbor = FakeHarbor()
    value = instance("registry.harbor.v1", {"project": "acme", "quotaGiB": 10, "robotDurationDays": 90, "identityUserRef": IDENTITY_USER}, "build-push")
    provider.reconcile_instance(kube, value, harbor_factory=lambda _kube: harbor, identity_factory=lambda: identity)
    assert kube.status["phase"] == "Ready"
    assert kube.status["reason"] is None
    assert kube.status["access"]["quotaGiB"] == 10
    assert kube.status["access"]["humanCredentialRef"].startswith("credential://harbor/acme/")
    assert "password" not in json.dumps(kube.status)
    assert harbor.quota == (7, 10)
    assert harbor.created_users == 1
    assert harbor.granted_role_id == 2


def test_new_tenant_uses_its_active_tenant_project_without_legacy_harbor_catalog():
    kube = FakeKube("registry.harbor.v1")
    kube.catalog = {}
    harbor = FakeHarbor()
    value = instance("registry.harbor.v1", {"project": "acme", "quotaGiB": 10, "robotDurationDays": 90, "identityUserRef": IDENTITY_USER}, "build-push")
    provider.reconcile_instance(kube, value, harbor_factory=lambda _kube: harbor, identity_factory=lambda: identity)
    assert kube.status["phase"] == "Ready"
    assert harbor.project["name"] == "acme"
    assert harbor.user["username"] == "acme"


def test_harbor_plan_maps_to_native_project_role():
    for plan, expected_role in (("pull", 3), ("build-push", 2), ("project-admin", 1)):
        kube = FakeKube("registry.harbor.v1")
        harbor = FakeHarbor()
        value = instance("registry.harbor.v1", {"project": "acme", "quotaGiB": 10, "robotDurationDays": 90, "identityUserRef": IDENTITY_USER}, plan)
        provider.reconcile_instance(kube, value, harbor_factory=lambda _kube: harbor, identity_factory=lambda: identity)
        assert harbor.granted_role_id == expected_role


def test_harbor_recreate_after_delete_recovers_managed_account_without_old_revoker():
    kube = FakeKube("registry.harbor.v1")
    harbor = FakeHarbor()
    old = instance("registry.harbor.v1", {"project": "acme", "quotaGiB": 10, "robotDurationDays": 90, "identityUserRef": IDENTITY_USER}, "pull")
    provider.reconcile_instance(kube, old, harbor_factory=lambda _kube: harbor, identity_factory=lambda: identity)
    old["status"] = kube.status
    old["spec"]["lifecycle"] = "Deleting"
    provider.reconcile_instance(kube, old, harbor_factory=lambda _kube: harbor, identity_factory=lambda: identity)
    old["status"] = kube.status
    assert old["status"]["phase"] == "Deleted" and harbor.members == []
    kube.instances = [old]
    new = instance("registry.harbor.v1", {"project": "acme", "quotaGiB": 10, "robotDurationDays": 90, "identityUserRef": IDENTITY_USER}, "pull")
    new["metadata"].update({"name": "replacement", "uid": "new-instance-uid"})
    provider.reconcile_instance(kube, new, harbor_factory=lambda _kube: harbor, identity_factory=lambda: identity)
    assert kube.status["phase"] == "Ready" and len(harbor.reset_passwords) == 1
    assert len(harbor.members) == 1 and harbor.created_users == 1
    provider.reconcile_instance(kube, old, harbor_factory=lambda _kube: harbor, identity_factory=lambda: identity)
    assert len(harbor.members) == 1


def test_harbor_refuses_to_take_over_unmanaged_existing_account():
    import pytest
    kube = FakeKube("registry.harbor.v1")
    harbor = FakeHarbor()
    harbor.project = {"project_id": 7, "name": "acme", "metadata": {"public": "false"}}
    harbor.user = {"user_id": 17, "username": "acme", "email": "admin@acme.example"}
    value = instance("registry.harbor.v1", {"project": "acme", "quotaGiB": 10, "robotDurationDays": 90, "identityUserRef": IDENTITY_USER}, "pull")
    with pytest.raises(RuntimeError, match="requires credential adoption"):
        provider.reconcile_instance(kube, value, harbor_factory=lambda _kube: harbor, identity_factory=lambda: identity)
    assert not harbor.reset_passwords


def test_revoked_supabase_membership_blocks_native_provider_creation():
    kube = FakeKube("registry.harbor.v1")
    harbor = FakeHarbor()
    value = instance("registry.harbor.v1", {"project": "acme", "quotaGiB": 10, "robotDurationDays": 90, "identityUserRef": IDENTITY_USER}, "pull")

    class RevokedIdentity(FakeIdentity):
        def human_email(self, tenant, identity_user):
            raise PermissionError("Supabase user is not an active member of the tenant")

    import pytest
    with pytest.raises(PermissionError, match="active member"):
        provider.reconcile_instance(kube, value, harbor_factory=lambda _kube: harbor, identity_factory=RevokedIdentity)
    assert harbor.project is None
    assert not kube.secrets


def test_revoked_membership_removes_existing_harbor_access_then_restores():
    kube = FakeKube("registry.harbor.v1")
    harbor = FakeHarbor()
    value = instance("registry.harbor.v1", {"project": "acme", "quotaGiB": 10, "robotDurationDays": 90, "identityUserRef": IDENTITY_USER}, "build-push")
    provider.reconcile_instance(kube, value, harbor_factory=lambda _kube: harbor, identity_factory=lambda: identity)
    value["status"] = kube.status
    harbor.robot_items = [{"id": 7, "level": "project", "disable": False, "permissions": [{"namespace": "acme"}]}]

    class RevokedIdentity(FakeIdentity):
        def human_email(self, tenant, identity_user):
            raise PermissionError("Supabase user is not an active member of the tenant")

    provider.reconcile_instance(kube, value, harbor_factory=lambda _kube: harbor, identity_factory=RevokedIdentity)
    assert kube.status["phase"] == "Suspended"
    assert kube.status["reason"] == "IdentityMembershipRevoked"
    assert harbor.members == [] and harbor.robot_items[0]["disable"] is True
    value["status"] = kube.status
    provider.reconcile_instance(kube, value, harbor_factory=lambda _kube: harbor, identity_factory=lambda: identity)
    assert kube.status["phase"] == "Ready"
    assert kube.status["reason"] is None
    assert harbor.members and harbor.robot_items[0]["disable"] is False


def test_harbor_suspend_revokes_member_and_project_robot_then_resumes():
    kube = FakeKube("registry.harbor.v1")
    harbor = FakeHarbor()
    value = instance("registry.harbor.v1", {"project": "acme", "quotaGiB": 10, "robotDurationDays": 90, "identityUserRef": IDENTITY_USER}, "build-push")
    provider.reconcile_instance(kube, value, harbor_factory=lambda _kube: harbor, identity_factory=lambda: identity)
    value["status"] = kube.status
    harbor.robot_items = [{"id": 7, "level": "project", "disable": False, "permissions": [{"namespace": "acme"}]}, {"id": 8, "level": "project", "disable": False, "permissions": [{"namespace": "other"}]}]
    value["spec"]["lifecycle"] = "Suspended"
    provider.reconcile_instance(kube, value, harbor_factory=lambda _kube: harbor, identity_factory=lambda: identity)
    assert kube.status["phase"] == "Suspended"
    assert kube.status["reason"] is None
    assert kube.status["suspendedRobotIds"] == [7]
    assert harbor.members == []
    assert [item["disable"] for item in harbor.robot_items] == [True, False]
    value["status"] = kube.status
    value["spec"]["lifecycle"] = "Requested"
    provider.reconcile_instance(kube, value, harbor_factory=lambda _kube: harbor, identity_factory=lambda: identity)
    assert kube.status["phase"] == "Ready"
    assert [item["disable"] for item in harbor.robot_items] == [False, False]
    value["status"] = kube.status
    provider.reconcile_instance(kube, value, harbor_factory=lambda _kube: harbor, identity_factory=lambda: identity)
    assert harbor.created_users == 1


def test_harbor_suspend_persists_restore_set_before_provider_write():
    kube = FakeKube("registry.harbor.v1")
    harbor = FakeHarbor()
    value = instance("registry.harbor.v1", {"project": "acme", "quotaGiB": 10, "robotDurationDays": 90, "identityUserRef": IDENTITY_USER}, "pull")
    value["spec"]["lifecycle"] = "Suspended"
    harbor.robot_items = [{"id": 7, "level": "project", "disable": False, "permissions": [{"namespace": "acme"}]}]

    def fail_disable(_robot_id, _disabled=True):
        raise RuntimeError("provider write failed")

    harbor.disable_robot = fail_disable
    import pytest
    with pytest.raises(RuntimeError, match="provider write failed"):
        provider.reconcile_instance(kube, value, harbor_factory=lambda _kube: harbor, identity_factory=lambda: identity)
    assert kube.status["phase"] == "Revoking"
    assert kube.status["suspendedRobotIds"] == [7]


class FakeGrafana:
    def __init__(self):
        self.human = None
        self.calls = []
        self.members = []
        self.reset_passwords = []

    def request(self, method, path, payload=None, org_id=None):
        self.calls.append((method, path, org_id))
        if path == "/api/orgs/4": return {"id": 4}
        if path == "/api/datasources": return [{"uid": "tenant-metrics"}]
        if path == "/api/datasources/uid/tenant-metrics":
            return {"uid": "tenant-metrics", "orgId": 4, "type": "prometheus", "access": "proxy",
                    "url": "http://tenant-metrics-auth.observability-system.svc:8427",
                    "basicAuth": True, "basicAuthUser": "acme-kube",
                    "secureJsonFields": {"basicAuthPassword": True}}
        if path == "/api/folders": return [{"uid": "tenant-folder"}]
        if path == "/api/orgs/4/users" and method == "GET": return list(self.members)
        if path == "/api/orgs/4/users/8" and method == "DELETE":
            self.members = []
            return {}
        raise AssertionError(path)

    def user(self, login):
        return self.human

    def ensure_human(self, org_id, login, email, role, password):
        assert (org_id, login, email, role) == (4, "acme-admin", "admin@acme.example", "Viewer")
        assert self.human is not None or password
        self.human = {"id": 8, "email": email}
        self.members = [{"userId": 8, "role": role}]
        return 8

    def reset_user_password(self, user_id, password):
        assert user_id == 8 and password
        self.reset_passwords.append(password)


GRAFANA_BINDING = {"orgId": 4, "tenantLabel": "acme", "datasourceBindings": {
    "tenant-metrics": {"url": "http://tenant-metrics-auth.observability-system.svc:8427", "basicAuthUser": "acme-kube"}}}


def test_grafana_reconcile_uses_tenant_org_and_managed_human_account(monkeypatch):
    kube = FakeKube("observability.grafana.v1")
    grafana = FakeGrafana()
    monkeypatch.setattr(provider, "tenant_config", lambda tenant: GRAFANA_BINDING)
    value = instance("observability.grafana.v1", {"datasourceRefs": ["tenant-metrics"], "folderRefs": [], "humanLogin": "acme-admin", "identityUserRef": IDENTITY_USER}, "viewer")
    provider.reconcile_instance(kube, value, grafana_factory=lambda _kube: grafana, identity_factory=lambda: identity)
    assert kube.status["phase"] == "Ready"
    assert kube.status["access"]["organizationId"] == 4
    assert kube.status["access"]["humanCredentialRef"].startswith("credential://grafana/acme/")
    assert "password" not in json.dumps(kube.status)


def test_grafana_auto_scope_creates_two_native_scoped_vmusers_and_datasources(monkeypatch):
    monkeypatch.delenv("GRAFANA_TENANT_BINDINGS_JSON")
    monkeypatch.setattr(provider.control, "runtime_namespace", lambda tenant: "acme" if tenant == "acme" else None)

    class AutoKube(FakeKube):
        def __init__(self):
            super().__init__("observability.grafana.v1")
            self.vmusers = {}

        def get(self, plural, name, namespace=None, api=None):
            if plural == "vmusers":
                if name not in self.vmusers:
                    raise urllib.error.HTTPError("https://kubernetes.invalid", 404, "not found", {}, None)
                return self.vmusers[name]
            return super().get(plural, name, namespace, api)

        def create(self, plural, value, namespace=None, api=None):
            if plural != "vmusers":
                return super().create(plural, value, namespace, api)
            assert namespace == "credential-broker" and api == provider.VMUSER_API
            assert value["spec"]["generatePassword"] is True
            name = value["metadata"]["name"]
            created = {**value, "metadata": {**value["metadata"], "uid": "uid-" + name}}
            self.vmusers[name] = created
            self.secrets[(namespace, "vmuser-" + name)] = {
                "metadata": {"ownerReferences": [{"uid": created["metadata"]["uid"], "kind": "VMUser"}]},
                "data": {"username": base64.b64encode(name.encode()).decode(),
                         "password": base64.b64encode(("password-" + name).encode()).decode()},
            }
            return created

    class AutoGrafana:
        def __init__(self):
            self.org = None
            self.datasources = {}
            self.members = [{"userId": 8, "role": "Viewer"}]

        def user(self, login):
            return {"id": 8, "email": "admin@acme.example"}

        def request(self, method, path, payload=None, org_id=None, *, missing_ok=False):
            if path.startswith("/api/orgs/name/") and method == "GET":
                return self.org
            if path == "/api/orgs" and method == "POST":
                self.org = {"id": 17, "name": payload["name"]}
                return self.org
            if path == "/api/datasources" and method == "GET":
                return list(self.datasources.values())
            if path == "/api/orgs/17/users" and method == "GET":
                return list(self.members)
            if path == "/api/orgs/17/users/8" and method == "DELETE":
                self.members = []
                return {}
            if path.startswith("/api/datasources/uid/") and method == "GET":
                return self.datasources.get(path.rsplit("/", 1)[-1])
            if path.startswith("/api/datasources/proxy/uid/") and method == "GET":
                return {"status": "success", "data": {"result": []}}
            if path == "/api/datasources" and method == "POST":
                assert org_id == 17 and payload["url"] == provider.METRICS_AUTH_URL
                assert payload["basicAuth"] is True and payload["secureJsonData"]["basicAuthPassword"]
                self.datasources[payload["uid"]] = {**payload, "id": len(self.datasources) + 1,
                    "orgId": org_id, "secureJsonFields": {"basicAuthPassword": True}}
                return {}
            raise AssertionError((method, path))

    kube, grafana = AutoKube(), AutoGrafana()
    value = instance("observability.grafana.v1", {"datasourceRefs": ["app", "kube"], "folderRefs": [],
        "humanLogin": "acme-admin", "identityUserRef": IDENTITY_USER}, "viewer")
    org_id, bindings = provider.ensure_auto_grafana_scope(kube, value, grafana)
    assert org_id == 17 and len(bindings) == 2
    assert set(grafana.datasources) == set(bindings)
    assert len(kube.vmusers) == 2
    urls = {name.rsplit("-", 1)[-1]: vmuser["spec"]["targetRefs"][0]["static"]["url"]
            for name, vmuser in kube.vmusers.items()}
    assert "extra_label=namespace%3Dacme" in urls["app"]
    assert "extra_label=exported_namespace%3Dacme" in urls["kube"]
    assert "extra_label=job%3Dre8ch-kube-state-metrics" in urls["kube"]
    assert all(v["metadata"]["labels"]["observability.re8ch.com/tenant-metrics-user"] == "true"
               for v in kube.vmusers.values())
    # Reconcile is idempotent; no new credentials or datasource writes are needed.
    assert provider.ensure_auto_grafana_scope(kube, value, grafana)[0] == 17
    value["status"] = {"access": {"organizationId": 17}}
    kube.vmusers[provider.auto_metrics_id("acme", "acme", "app")]["spec"]["targetRefs"][0]["static"]["url"] = "http://unscoped.invalid"
    with pytest.raises(PermissionError, match="VMUser tenant scope"):
        provider.ensure_auto_grafana_scope(kube, value, grafana)
    assert grafana.members == []
    value["metadata"]["namespace"] = "other"
    with pytest.raises(PermissionError, match="outside the active tenant"):
        provider.ensure_auto_grafana_scope(kube, value, grafana)


def test_grafana_reconcile_rejects_unscoped_datasource_in_tenant_org(monkeypatch):
    class UnsafeGrafana(FakeGrafana):
        def request(self, method, path, payload=None, org_id=None):
            if path == "/api/datasources":
                return [{"uid": "tenant-metrics"}, {"uid": "platform-metrics"}]
            return super().request(method, path, payload, org_id)

    monkeypatch.setattr(provider, "tenant_config", lambda tenant: GRAFANA_BINDING)
    grafana = UnsafeGrafana()
    grafana.human = {"id": 8, "email": "admin@acme.example"}
    grafana.members = [{"userId": 8, "role": "Viewer"}]
    value = instance("observability.grafana.v1", {"datasourceRefs": ["tenant-metrics"], "folderRefs": [], "humanLogin": "acme-admin", "identityUserRef": IDENTITY_USER}, "viewer")
    with pytest.raises(PermissionError, match="datasource scope"):
        provider.reconcile_grafana(FakeKube("observability.grafana.v1"), value, grafana, "admin@acme.example")
    assert grafana.members == []


def test_grafana_reconcile_rejects_datasource_without_scoped_auth(monkeypatch):
    class UnsafeGrafana(FakeGrafana):
        def request(self, method, path, payload=None, org_id=None):
            result = super().request(method, path, payload, org_id)
            if path == "/api/datasources/uid/tenant-metrics":
                return {**result, "url": "http://vmselect-re8ch-metrics:8481", "basicAuth": False}
            return result

    monkeypatch.setattr(provider, "tenant_config", lambda tenant: GRAFANA_BINDING)
    value = instance("observability.grafana.v1", {"datasourceRefs": ["tenant-metrics"], "folderRefs": [], "humanLogin": "acme-admin", "identityUserRef": IDENTITY_USER}, "viewer")
    with pytest.raises(PermissionError, match="authentication or boundary"):
        provider.reconcile_grafana(FakeKube("observability.grafana.v1"), value, UnsafeGrafana(), "admin@acme.example")


def test_grafana_suspend_revokes_org_membership(monkeypatch):
    kube = FakeKube("observability.grafana.v1")
    grafana = FakeGrafana()
    monkeypatch.setattr(provider, "tenant_config", lambda tenant: GRAFANA_BINDING)
    value = instance("observability.grafana.v1", {"datasourceRefs": ["tenant-metrics"], "folderRefs": [], "humanLogin": "acme-admin", "identityUserRef": IDENTITY_USER}, "viewer")
    provider.reconcile_instance(kube, value, grafana_factory=lambda _kube: grafana, identity_factory=lambda: identity)
    value["status"] = kube.status
    value["spec"]["lifecycle"] = "Suspended"
    provider.reconcile_instance(kube, value, grafana_factory=lambda _kube: grafana, identity_factory=lambda: identity)
    assert kube.status["phase"] == "Suspended"
    assert grafana.members == []


def test_revoked_membership_removes_existing_grafana_access_then_restores(monkeypatch):
    kube = FakeKube("observability.grafana.v1")
    grafana = FakeGrafana()
    monkeypatch.setattr(provider, "tenant_config", lambda tenant: GRAFANA_BINDING)
    value = instance("observability.grafana.v1", {"datasourceRefs": ["tenant-metrics"], "folderRefs": [], "humanLogin": "acme-admin", "identityUserRef": IDENTITY_USER}, "viewer")
    provider.reconcile_instance(kube, value, grafana_factory=lambda _kube: grafana, identity_factory=lambda: identity)
    value["status"] = kube.status

    class RevokedIdentity(FakeIdentity):
        def human_email(self, tenant, identity_user):
            raise PermissionError("Supabase user is not an active member of the tenant")

    provider.reconcile_instance(kube, value, grafana_factory=lambda _kube: grafana, identity_factory=RevokedIdentity)
    assert kube.status["phase"] == "Suspended"
    assert kube.status["reason"] == "IdentityMembershipRevoked"
    assert grafana.members == []
    value["status"] = kube.status
    provider.reconcile_instance(kube, value, grafana_factory=lambda _kube: grafana, identity_factory=lambda: identity)
    assert kube.status["phase"] == "Ready" and grafana.members


def test_grafana_recreate_after_delete_restores_same_login(monkeypatch):
    kube = FakeKube("observability.grafana.v1")
    grafana = FakeGrafana()
    monkeypatch.setattr(provider, "tenant_config", lambda tenant: GRAFANA_BINDING)
    parameters = {"datasourceRefs": ["tenant-metrics"], "folderRefs": [], "humanLogin": "acme-admin", "identityUserRef": IDENTITY_USER}
    old = instance("observability.grafana.v1", parameters, "viewer")
    provider.reconcile_instance(kube, old, grafana_factory=lambda _kube: grafana, identity_factory=lambda: identity)
    old["status"] = kube.status
    old["spec"]["lifecycle"] = "Deleting"
    provider.reconcile_instance(kube, old, grafana_factory=lambda _kube: grafana, identity_factory=lambda: identity)
    old["status"] = kube.status
    assert old["status"]["phase"] == "Deleted" and grafana.members == []
    kube.instances = [old]
    new = instance("observability.grafana.v1", parameters, "viewer")
    new["metadata"].update({"name": "replacement", "uid": "new-instance-uid"})
    provider.reconcile_instance(kube, new, grafana_factory=lambda _kube: grafana, identity_factory=lambda: identity)
    assert kube.status["phase"] == "Ready" and len(grafana.reset_passwords) == 1
    assert grafana.members == [{"userId": 8, "role": "Viewer"}]
    provider.reconcile_instance(kube, old, grafana_factory=lambda _kube: grafana, identity_factory=lambda: identity)
    assert grafana.members == [{"userId": 8, "role": "Viewer"}]


def test_grafana_reconcile_rejects_raw_datasource(monkeypatch):
    kube = FakeKube("observability.grafana.v1")
    grafana = FakeGrafana()
    monkeypatch.setattr(provider, "tenant_config", lambda tenant: GRAFANA_BINDING)
    value = instance("observability.grafana.v1", {"datasourceRefs": ["pigsty-metrics"], "folderRefs": [], "humanLogin": "acme-admin", "identityUserRef": IDENTITY_USER}, "viewer")
    try:
        provider.reconcile_instance(kube, value, grafana_factory=lambda _kube: grafana, identity_factory=lambda: identity)
    except PermissionError as exc:
        assert "scope" in str(exc)
    else:
        raise AssertionError("raw datasource must be denied")
