import importlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch


class GoTrueOAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.secret = tempfile.NamedTemporaryFile()
        cls.secret.write(b"test-secret")
        cls.secret.flush()
        with patch.dict(os.environ, {"HMAC_SECRET_PATH": cls.secret.name}):
            cls.app = importlib.reload(importlib.import_module("app"))

    @classmethod
    def tearDownClass(cls):
        cls.secret.close()

    def test_linked_github_id_preserves_existing_device_owner(self):
        token = self.app.seal({"access_token": "gotrue-token"}, "session")
        user = {"id": "supabase-user", "identities": [{"provider": "github", "id": "61690719"}]}
        cache = {"members": {"hliu74": {"githubId": "61690719", "authorized": True}}}
        with patch.object(self.app, "auth_state", return_value=cache), \
                patch.object(self.app.urllib.request, "urlopen") as open_url:
            open_url.return_value.__enter__.return_value.read.return_value = json.dumps(user).encode()
            self.assertEqual(self.app.identity({"Cookie": f"other=x; {self.app.SESSION_COOKIE}={token}",
                                                "X-Auth-Request-User": "attacker"}), "hliu74")
            self.assertTrue(self.app.owned_by({"subject": "hliu74"}, "hliu74"))
            request = open_url.call_args.args[0]
            self.assertEqual(request.get_header("Authorization"), "Bearer gotrue-token")
            self.assertTrue(request.full_url.endswith("/auth/v1/user"))

    def test_email_or_unlinked_github_identity_cannot_claim_membership(self):
        token = self.app.seal({"access_token": "gotrue-token"}, "session")
        user = {"id": "other-user", "email": "harry@re8ch.com", "identities": [{"provider": "github", "id": "wrong"}]}
        cache = {"members": {"hliu74": {"githubId": "61690719", "authorized": True}}}
        with patch.object(self.app, "auth_state", return_value=cache), \
                patch.object(self.app.urllib.request, "urlopen") as open_url:
            open_url.return_value.__enter__.return_value.read.return_value = json.dumps(user).encode()
            with self.assertRaises(PermissionError):
                self.app.identity({"Cookie": f"{self.app.SESSION_COOKIE}={token}"})

    def test_cookie_tamper_and_cross_origin_write_fail_closed(self):
        with self.assertRaises(PermissionError):
            self.app.identity({"X-Auth-Request-User": "hliu74"})
        token = self.app.seal({"access_token": "gotrue-token"}, "session")
        with self.assertRaises(PermissionError):
            self.app.unseal(token + "x", "session", 3600)
        self.assertFalse(self.app.same_origin({"Origin": "https://evil.example"}))
        self.assertTrue(self.app.same_origin({"Origin": "https://sub.access.re8ch.com"}))


if __name__ == "__main__":
    unittest.main()
