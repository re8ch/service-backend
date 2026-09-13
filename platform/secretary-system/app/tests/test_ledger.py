import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from secretary.ledger import PostgresLedger


class LedgerConnectionTest(unittest.TestCase):
    def test_connection_uses_autocommit_for_read_boundaries(self):
        connect = Mock(return_value=object())
        psycopg = types.SimpleNamespace(connect=connect)

        with patch.dict(sys.modules, {"psycopg": psycopg}):
            PostgresLedger("postgresql://example/secretary")

        connect.assert_called_once_with("postgresql://example/secretary", autocommit=True)


if __name__ == "__main__":
    unittest.main()
