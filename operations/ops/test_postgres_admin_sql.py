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

    def test_billing_ownership_repair_is_fixed_and_uses_postgres(self):
        args = {"approvalId": "re8ch/service-backend#123"}
        planned = OPS.repair_re8ch_billing_ownership(args)
        args.update({"apply": True, "planHash": planned["planHash"]})
        secret = base64.b64encode(b"not-a-real-password").decode("ascii")
        fake_socket = mock.Mock()
        with mock.patch.object(OPS, "kube_request", return_value={"data": {"POSTGRES_PASSWORD": secret}}), \
             mock.patch.object(OPS, "pg_connect", return_value=fake_socket) as connect, \
             mock.patch.object(OPS, "pg_execute_simple", return_value={"command": "COMMIT"}):
            result = OPS.repair_re8ch_billing_ownership(args)
        self.assertEqual(result["status"], "executed")
        self.assertEqual(connect.call_args.args[3], "postgres")

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
