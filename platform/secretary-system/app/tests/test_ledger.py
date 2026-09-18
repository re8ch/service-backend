import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from secretary.ledger import PostgresLedger


class LedgerConnectionTest(unittest.TestCase):
    def test_constructor_does_not_open_a_stale_global_connection(self):
        connect = Mock(return_value=object())
        psycopg = types.SimpleNamespace(connect=connect)

        with patch.dict(sys.modules, {"psycopg": psycopg}):
            PostgresLedger("postgresql://example/secretary")

        connect.assert_not_called()

    def test_connection_is_bounded(self):
        connect = Mock(return_value=object())
        psycopg = types.SimpleNamespace(connect=connect)
        ledger = PostgresLedger("postgresql://example/secretary")
        with patch.dict(sys.modules, {"psycopg": psycopg}):
            ledger._connect()
        connect.assert_called_once_with(
            "postgresql://example/secretary", autocommit=True, connect_timeout=3,
            options="-c statement_timeout=5000 -c lock_timeout=3000",
        )

    def test_database_host_override_preserves_credentials(self):
        with patch.dict("os.environ", {"SECRETARY_DATABASE_HOST_OVERRIDE": "pgwrite.example", "SECRETARY_DATABASE_PORT_OVERRIDE": "5432"}):
            ledger = PostgresLedger("postgresql://user:p%40ss@old.example:5432/secretary?sslmode=require")
        self.assertEqual(ledger.dsn, "postgresql://user:p%40ss@pgwrite.example:5432/secretary?sslmode=require")


if __name__ == "__main__":
    unittest.main()
