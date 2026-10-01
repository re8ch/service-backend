import importlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch


class AuthGatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.secret = tempfile.NamedTemporaryFile()
        cls.secret.write(b"test-secret")
        cls.secret.flush()
        with patch.dict(os.environ, {"HMAC_SECRET_PATH": cls.secret.name,
                                  "IDENTITY_ALIASES_JSON": '{"harry@re8ch.com":"hliu74"}'}):
            cls.app = importlib.reload(importlib.import_module("app"))

    @classmethod
    def tearDownClass(cls):
        cls.secret.close()

    def test_verified_gateway_identity_preserves_legacy_devices(self):
        profile = {"identity": {"subject": "auth-user-id", "email": "harry@re8ch.com", "emailVerified": True}}
        with patch.object(self.app.urllib.request, "urlopen") as open_url:
            open_url.return_value.__enter__.return_value.read.return_value = json.dumps(profile).encode()
            self.assertEqual(self.app.identity({"Cookie": "other=ignored; re8ch_sso=session",
                                                "X-Auth-Request-User": "attacker"}), "hliu74")
            self.assertTrue(self.app.owned_by({"subject": "hliu74"}, "hliu74"))
            request = open_url.call_args.args[0]
            self.assertEqual(request.get_header("Cookie"), "re8ch_sso=session")

    def test_unverified_email_cannot_bind_legacy_account(self):
        profile = {"identity": {"subject": "different-user", "email": "harry@re8ch.com", "emailVerified": False}}
        with patch.object(self.app.urllib.request, "urlopen") as open_url:
            open_url.return_value.__enter__.return_value.read.return_value = json.dumps(profile).encode()
            self.assertEqual(self.app.identity({"Cookie": "re8ch_sso=session"}), "different-user")

    def test_no_session_and_cross_origin_write_fail_closed(self):
        with self.assertRaises(PermissionError):
            self.app.identity({"X-Auth-Request-User": "hliu74"})
        self.assertFalse(self.app.same_origin({"Origin": "https://evil.example"}))
        self.assertTrue(self.app.same_origin({"Origin": "https://sub.access.re8ch.com"}))


if __name__ == "__main__":
    unittest.main()
