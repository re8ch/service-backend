import base64
import importlib.util
import pathlib
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "clusters/re8ch/components/ops/ops-mcp-server-app.py"
SPEC = importlib.util.spec_from_file_location("ops_mcp_server_app", MODULE_PATH)
OPS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OPS)


class PostgresAdminSQLTests(unittest.TestCase):
    def test_postgres_md5_password_authentication_is_rejected(self):
        with self.assertRaisesRegex(OPS.PgProtocolError, "require SCRAM-SHA-256"):
            OPS.pg_authenticate(mock.Mock(), "postgres", "not-a-real-password", b"\x00\x00\x00\x05salt")

    def test_billing_write_sets_migrator_role(self):
        args = {"database": "re8ch_billing", "sql": "select 1", "readOnly": False,
                "approvalId": "re8ch/cloud-functions#141"}
        planned = OPS.postgres_admin_sql(args)
        args.update({"apply": True, "planHash": planned["planHash"]})
        secret = base64.b64encode(b"not-a-real-password").decode("ascii")
        fake_socket = mock.Mock()
        with mock.patch.object(OPS, "kube_request", return_value={"data": {"POSTGRES_PASSWORD": secret}}), \
             mock.patch.object(OPS, "pg_connect", return_value=fake_socket), \
             mock.patch.object(OPS, "pg_execute_simple", return_value={"command": "COMMIT"}) as execute:
            result = OPS.postgres_admin_sql(args)
        self.assertEqual(result["status"], "executed")
        self.assertTrue(any(call.args[1] == "SET ROLE re8ch_billing_migrator" for call in execute.call_args_list))

    def test_billing_ownership_repair_is_fixed_and_uses_supabase_admin(self):
        args = {"approvalId": "re8ch/service-backend#123"}
        planned = OPS.repair_re8ch_billing_ownership(args)
        args.update({"apply": True, "planHash": planned["planHash"]})
        secret = base64.b64encode(b"not-a-real-password").decode("ascii")
        fake_socket = mock.Mock()
        with mock.patch.object(OPS, "kube_request", return_value={"data": {"POSTGRES_PASSWORD": secret}}), \
             mock.patch.object(OPS, "pg_connect", return_value=fake_socket) as connect, \
             mock.patch.object(OPS, "pg_execute_simple", return_value={"command": "COMMIT"}) as execute:
            result = OPS.repair_re8ch_billing_ownership(args)
        self.assertEqual(result["status"], "executed")
        self.assertEqual(connect.call_args.args[3], "supabase_admin")
        repair_sql = next(call.args[1] for call in execute.call_args_list if "CREATE ROLE re8ch_billing_owner" in call.args[1])
        self.assertNotIn("'S'", repair_sql)
        self.assertNotIn("ALTER SEQUENCE", repair_sql)
        self.assertIn("AND NOT c.relispartition", repair_sql)
        self.assertNotIn("IN ('postgres','supabase_admin')", repair_sql)
        self.assertEqual(repair_sql.count("= current_user"), 3)
        self.assertIn("TO re8ch_billing_owner, re8ch_billing_migrator", repair_sql)
        self.assertLess(repair_sql.index("GRANT USAGE, CREATE ON SCHEMA"), repair_sql.index("ALTER DATABASE re8ch_billing OWNER"))

    def test_apply_returns_structured_postgres_rejection(self):
        args = {
            "database": "re8ch_billing",
            "sql": "grant commerce_anon to re8ch_commerce_pgrst",
            "readOnly": False,
            "approvalId": "re8ch/cloud-functions#123",
        }
        planned = OPS.postgres_admin_sql(args)
        args.update({"apply": True, "planHash": planned["planHash"]})

        calls = 0

        def execute(_sock, _sql, max_rows=100):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"command": "SET"}
            raise OPS.PgProtocolError(
                "permission denied to grant role commerce_anon",
                sqlstate="42501",
                severity="ERROR",
            )

        secret = base64.b64encode(b"not-a-real-password").decode("ascii")
        fake_socket = mock.Mock()
        with mock.patch.object(
            OPS,
            "kube_request",
            return_value={"data": {"POSTGRES_PASSWORD": secret}},
        ), mock.patch.object(OPS, "pg_connect", return_value=fake_socket), mock.patch.object(
            OPS, "pg_execute_simple", side_effect=execute
        ):
            result = OPS.postgres_admin_sql(args)

        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["error"]["sqlState"], "42501")
        self.assertIn("commerce_anon", result["error"]["message"])

    def test_protocol_error_extracts_sqlstate(self):
        payload = b"SERROR\x00VERROR\x00C42501\x00Mpermission denied\x00\x00"
        error = OPS.pg_protocol_error(payload)
        self.assertEqual(error.sqlstate, "42501")
        self.assertEqual(error.severity, "ERROR")
        self.assertEqual(str(error), "permission denied")


if __name__ == "__main__":
    unittest.main()
