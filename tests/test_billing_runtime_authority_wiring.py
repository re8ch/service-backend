from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "operations/supabase-billing"


def docs(name):
    return [item for item in yaml.safe_load_all((BASE / name).read_text()) if item]


@pytest.fixture
def publication_database(tmp_path):
    if not all(shutil.which(name) for name in ("initdb", "pg_ctl", "psql")):
        pytest.skip("local PostgreSQL binaries unavailable")
    data = tmp_path / "data"
    socket = Path(tempfile.mkdtemp(prefix="billing-authority-pg-", dir="/tmp"))
    subprocess.run(["initdb", "-D", str(data), "-A", "trust", "--no-instructions"],
                   check=True, capture_output=True, text=True)
    subprocess.run(["pg_ctl", "-D", str(data), "-l", str(tmp_path / "postgres.log"),
                    "-o", f"-k {socket} -c listen_addresses=''", "-w", "start"],
                   check=True, capture_output=True, text=True)

    def sql(statement):
        result = subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-h", str(socket),
                                 "-d", "postgres", "-Atqc", statement],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    try:
        sql("""
          create role re8ch_billing_owner;
          create role re8ch_billing_migrator;
          create role billing_commerce_runtime;
          create role billing_supabase_pgrst_rw login;
          create role billing_supabase_pgrst_ro login;
          grant re8ch_billing_owner to re8ch_billing_migrator;
          grant billing_commerce_runtime to re8ch_billing_migrator with admin option;
          alter database postgres owner to re8ch_billing_owner;
        """)
        sql((BASE / "tool-publication-authority.sql").read_text())
        yield sql
    finally:
        subprocess.run(["pg_ctl", "-D", str(data), "-m", "immediate", "-w", "stop"],
                       check=True, capture_output=True, text=True)
        socket.rmdir()


def test_authority_sql_is_packaged_as_versioned_migrations_not_registration_gitops():
    kustomization = yaml.safe_load((BASE / "kustomization.yaml").read_text())
    assert "runtime-authority-migrations.yaml" in kustomization["resources"]
    generators = {item["name"]: item for item in kustomization["configMapGenerator"]}
    assert generators["supabase-billing-tool-publication-authority-v1"]["files"] == [
        "migration.sql=tool-publication-authority.sql"
    ]
    assert generators["supabase-billing-org-cluster-bindings-v1"]["files"] == [
        "migration.sql=org-cluster-bindings.sql"
    ]
    readme = (BASE / "README.md").read_text()
    assert "never create a per-registration GitOps object" in readme


def test_migration_jobs_reuse_managed_supabase_admin_credential_without_copying_it():
    manifests = docs("runtime-authority-migrations.yaml")
    jobs = [item for item in manifests if item["kind"] == "Job"]
    assert {job["metadata"]["name"] for job in jobs} == {
        "supabase-billing-tool-publication-authority-v1",
        "supabase-billing-org-cluster-bindings-v1",
    }
    for job in jobs:
        pod = job["spec"]["template"]["spec"]
        assert pod["automountServiceAccountToken"] is False
        assert pod["serviceAccountName"] == "supabase-billing-schema-migrator"
        container = pod["containers"][0]
        assert job["metadata"]["namespace"] == "supabase-pg-re8ch"
        env = {item["name"]: item for item in container["env"]}
        assert env["PGHOST"]["value"] == "pg-re8ch-primary.pigsty-external.svc.cluster.local"
        assert env["PGPORT"]["value"] == "5432"
        assert env["PGDATABASE"]["value"] == "re8ch_billing"
        assert env["PGUSER"]["valueFrom"]["secretKeyRef"] == {
            "name": "supabase-pg-re8ch-secrets", "key": "POSTGRES_USER_READ_WRITE"
        }
        assert env["PGPASSWORD"]["valueFrom"]["secretKeyRef"] == {
            "name": "supabase-pg-re8ch-secrets", "key": "POSTGRES_PASSWORD"
        }
        assert "BILLING_MIGRATOR_DATABASE_URL" not in env
        assert "PGPASSWORD" not in container["args"][0]
        assert "ON_ERROR_STOP=1" in container["args"][0]
        assert container["securityContext"]["readOnlyRootFilesystem"] is True
        assert pod["volumes"][0]["configMap"]["name"] == job["metadata"]["name"]

    account = next(item for item in manifests if item["kind"] == "ServiceAccount")
    assert account["automountServiceAccountToken"] is False
    policy = next(item for item in manifests if item["kind"] == "CiliumNetworkPolicy")
    assert policy["spec"]["endpointSelector"]["matchLabels"]["app.kubernetes.io/component"] == "schema-migration"
    rendered = str(policy["spec"]["egress"])
    assert "pg-re8ch-primary" in rendered and "5432" in rendered and "kube-dns" in rendered


def test_commerce_postgrest_and_target_runtime_role_are_wired():
    postgrest = (BASE / "postgrest-runtime.yaml").read_text()
    assert postgrest.count('PGRST_DB_SCHEMAS, value: "payment_api,commerce_api"') == 1
    assert postgrest.count('PGRST_DB_SCHEMAS, value: "payment_api,commerce_api,bifurcate_api"') == 1
    assert "PGRST_DB_SCHEMAS, value: \"bifurcate" not in postgrest
    cluster_sql = (BASE / "org-cluster-bindings.sql").read_text()
    for rpc in ("register_active_org_cluster_binding", "revoke_active_org_cluster_binding",
                "verify_active_org_cluster_binding"):
        assert f"commerce_api.{rpc}" in cluster_sql
    tool_sql = (BASE / "tool-publication-authority.sql").read_text()
    assert "grant billing_commerce_runtime to billing_supabase_pgrst_rw" in tool_sql
    assert "grant billing_commerce_runtime to billing_supabase_pgrst_ro" in tool_sql
    assert "notify pgrst, 'reload schema'" in tool_sql


def test_tool_authority_migration_grants_only_runtime_projection(publication_database):
    sql = publication_database
    assert sql("select pg_has_role('billing_supabase_pgrst_rw','billing_commerce_runtime','member')") == "t"
    assert sql("select pg_has_role('billing_supabase_pgrst_ro','billing_commerce_runtime','member')") == "t"
    assert sql("select has_schema_privilege('billing_commerce_runtime','bifurcate','usage')") == "t"
    assert sql("select has_table_privilege('billing_commerce_runtime','bifurcate.ready_tool_bindings','select')") == "t"
    assert sql("select has_table_privilege('billing_commerce_runtime','bifurcate.tool_publications','select')") == "f"


def test_migrations_switch_only_to_bounded_migrator_role():
    for name in ("tool-publication-authority.sql", "org-cluster-bindings.sql"):
        migration = (BASE / name).read_text().lower()
        assert migration.count("set local role re8ch_billing_migrator") == 1
        assert "set session authorization" not in migration
        assert "reset role" not in migration
