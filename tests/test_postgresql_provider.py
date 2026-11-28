import base64
import importlib.util
import inspect
import json
import sys
import types
import urllib.error
from pathlib import Path


ROOT = Path(__file__).parents[1]
CONTROL = ROOT / "clusters/re8ch/components/ai-operations/tenant_control_controller.py"
control_spec = importlib.util.spec_from_file_location("tenant_control_controller", CONTROL)
control = importlib.util.module_from_spec(control_spec)
control_spec.loader.exec_module(control)
sys.modules["tenant_control_controller"] = control
MODULE = ROOT / "platform/apps/postgresql-provider/postgresql_provider.py"
provider_spec = importlib.util.spec_from_file_location("postgresql_provider", MODULE)
provider = importlib.util.module_from_spec(provider_spec)
provider_spec.loader.exec_module(provider)


class FakeKube:
    def __init__(self):
        self.secrets = {"mcp-catalog-database": {"metadata": {"labels": {}}, "data": {}}}
        self.status = None

    def get(self, plural, name, namespace=None, api=None):
        if plural == "services":
            return {"spec": {"clusterIP": "10.0.0.10"}}
        if plural == "secrets" and name in self.secrets:
            return self.secrets[name]
        raise urllib.error.HTTPError("https://kube", 404, "not found", {}, None)

    def create(self, plural, value, namespace=None, api=None):
        assert plural == "secrets" and namespace == "credential-broker"
        encoded = {key: base64.b64encode(item.encode()).decode() for key, item in value["stringData"].items()}
        self.secrets[value["metadata"]["name"]] = {"metadata": value["metadata"], "data": encoded}
        return self.secrets[value["metadata"]["name"]]

    def patch(self, plural, name, value, namespace=None, api=None):
        assert plural == "secrets" and namespace in {"credential-broker", "tenant-infrastructure"}
        secret = self.secrets[name]
        secret["metadata"]["labels"].update(value["metadata"]["labels"])
        for key, item in value.get("stringData", {}).items():
            secret["data"][key] = base64.b64encode(item.encode()).decode()
        return secret

    def patch_status(self, plural, name, value, namespace=None):
        self.status = value


class FakePigsty:
    calls = []

    def __init__(self, kube):
        pass

    def ensure(self, database, role, password, extensions):
        self.calls.append(("ensure", database, role, bool(password), tuple(extensions)))

    def ensure_catalog_runtime(self, role, password):
        self.calls.append(("ensure_catalog_runtime", role, bool(password)))

    def login(self, role, enabled):
        self.calls.append(("login", role, enabled))

    def drop(self, database, role):
        self.calls.append(("drop", database, role))


def instance(tenant="acme", name="orders", lifecycle="Requested", parameters=None):
    if parameters is None:
        parameters = {"storageGiB": 20}
    return {"metadata": {"name": name, "namespace": tenant, "uid": "uid-123", "generation": 1,
                         "annotations": {"credentials.re8ch.com/rotation": "1"},
                         "labels": {"tenancy.re8ch.com/tenant": tenant}},
            "spec": {"tenantRef": tenant, "serviceClass": provider.SERVICE_CLASS, "plan": "standard",
                     "lifecycle": lifecycle, "parameters": parameters}}


def test_tenant_database_is_deterministic_and_credential_is_opaque():
    kube = FakeKube(); FakePigsty.calls = []
    provider.reconcile(kube, instance(), FakePigsty)
    assert kube.status["phase"] == "Ready"
    assert kube.status["access"]["accessRef"].startswith("credential://postgresql/acme/")
    assert kube.status["access"]["databaseRef"].startswith("pigsty://pg-re8ch/t_acme_orders_")
    serialized = json.dumps(kube.status).lower()
    assert "password" not in serialized and "host" not in serialized
    assert FakePigsty.calls[0][0] == "ensure"


def test_suspend_and_delete_use_pigsty_role_and_database_lifecycle():
    kube = FakeKube(); FakePigsty.calls = []
    ready = instance(); provider.reconcile(kube, ready, FakePigsty)
    provider.reconcile(kube, instance(lifecycle="Suspended"), FakePigsty)
    assert kube.status["phase"] == "Suspended" and FakePigsty.calls[-1][0] == "login"
    provider.reconcile(kube, instance(lifecycle="Deleting"), FakePigsty)
    assert kube.status["phase"] == "Deleted" and FakePigsty.calls[-1][0] == "drop"


def test_admin_authority_is_adopted_but_not_provisioned():
    kube = FakeKube(); FakePigsty.calls = []
    provider.reconcile(kube, instance("admin", "postgresql-shared", parameters={}), FakePigsty)
    assert kube.status["phase"] == "Ready"
    assert kube.status["access"]["authorityRefs"] == ["pg-identity", "pg-billing", "pg-re8ch"]
    assert FakePigsty.calls == []


def test_catalog_runtime_uses_fixed_existing_database_profile_and_broker_url():
    kube = FakeKube(); FakePigsty.calls = []
    value = instance("platform", "mcp-catalog-runtime", parameters={"profile": provider.CATALOG_RUNTIME_PROFILE})
    provider.reconcile(kube, value, FakePigsty)
    assert kube.status["phase"] == "Ready"
    assert kube.status["access"]["databaseRef"] == "pigsty://pg-re8ch/re8ch_billing"
    assert kube.status["access"]["privilegeProfile"] == provider.CATALOG_RUNTIME_PROFILE
    secret = kube.secrets["postgresql-mcp-catalog-runtime"]
    assert secret["metadata"]["labels"]["credentials.re8ch.com/type"] == "catalog-runtime"
    assert secret["metadata"]["labels"]["credentials.re8ch.com/rotation"] == "1"
    assert base64.b64decode(secret["data"]["MCP_CATALOG_DATABASE_URL"]).decode().startswith(
        "postgresql://mcp_catalog_runtime:")
    assert "MCP_CATALOG_DATABASE_URL" not in json.dumps(kube.status)
    projection = kube.secrets["mcp-catalog-database"]
    assert set(projection["data"]) == {"MCP_CATALOG_DATABASE_URL"}
    assert FakePigsty.calls == [("ensure_catalog_runtime", "mcp_catalog_runtime", True)]


def test_catalog_runtime_rejects_privilege_parameters_and_deletion():
    kube = FakeKube()
    for parameters in ({"profile": provider.CATALOG_RUNTIME_PROFILE, "schema": "public"}, {}):
        try:
            provider.reconcile(kube, instance("platform", "mcp-catalog-runtime", parameters=parameters), FakePigsty)
        except PermissionError:
            pass
        else:
            raise AssertionError("Catalog runtime accepted privilege input")


def test_catalog_runtime_sql_profile_cannot_mutate_catalog_authority():
    source = inspect.getsource(provider.Pigsty.ensure_catalog_runtime)
    assert "active_mcp_catalog" in source
    assert "claim_mcp_catalog_projection_events" in source
    assert "ack_mcp_catalog_projection_event" in source
    assert "request_mcp_catalog_registration" not in source
    assert "transition_mcp_catalog_registration" not in source
    assert "activate_mcp_catalog_revision" not in source
    assert "NOINHERIT" in source and "CONNECTION LIMIT 20" in source


def test_pigsty_connection_uses_stable_application_name(monkeypatch):
    calls = []

    class Connection:
        autocommit = False

    connection = Connection()
    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(
        connect=lambda **kwargs: calls.append(kwargs) or connection,
    ))
    pigsty = provider.Pigsty.__new__(provider.Pigsty)
    pigsty.host, pigsty.port, pigsty.user, pigsty.password = "db", "5432", "role", "password"
    assert pigsty.connect("re8ch_billing") is connection
    assert connection.autocommit is True
    assert calls == [{
        "host": "db", "port": "5432", "user": "role", "password": "password",
        "dbname": "re8ch_billing", "connect_timeout": 10,
        "application_name": "re8ch-postgresql-provider",
    }]


def test_parameter_and_identity_guards():
    assert provider.validate_parameters({"storageGiB": 20, "extensions": ["pgcrypto"]})["backupClass"] == "pigsty-pitr"
    for value in ({"storageGiB": 0}, {"storageGiB": 20, "extensions": ["file_fdw"]}, {"storageGiB": 20, "password": "x"}):
        try:
            provider.validate_parameters(value)
        except PermissionError:
            pass
        else:
            raise AssertionError("unsafe PostgreSQL parameters accepted")
    database, role = provider.database_identity("admin", "pg-re8ch", "uid")
    assert database.startswith("t_admin_pg_re8ch_") and role.endswith("_owner")


def test_provider_is_not_an_mcp_server_or_catalog_registrant():
    source = MODULE.read_text()
    assert "postgresql_provider_status" not in source
    assert "def register(" not in source
    assert 'self.path in {"/livez", "/readyz"}' in source
