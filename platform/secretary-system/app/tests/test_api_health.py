import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from secretary import api


class _UnavailableLedger:
    def ping(self):
        raise TimeoutError("database timeout")


class ApiHealthTest(unittest.TestCase):
    def test_liveness_does_not_depend_on_database(self):
        with patch.object(api, "db", side_effect=AssertionError("must not access database")):
            self.assertEqual(api.health(), {"ok": True})

    def test_readiness_is_503_when_database_is_unavailable(self):
        with patch.object(api, "db", return_value=_UnavailableLedger()):
            response = asyncio.run(api.ready())
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b"database timeout", response.body)


if __name__ == "__main__":
    unittest.main()
