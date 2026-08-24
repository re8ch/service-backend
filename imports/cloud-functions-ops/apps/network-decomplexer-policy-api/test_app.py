import base64
import gzip
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MODULE_PATH = Path(__file__).with_name("app.py")
SPEC = importlib.util.spec_from_file_location("network_decomplexer_policy_api", MODULE_PATH)
assert SPEC and SPEC.loader
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)


class PolicyTests(unittest.TestCase):
    def test_envelope_signature_covers_canonical_payload(self):
        key = Ed25519PrivateKey.generate()
        envelope = app.signed_envelope({"version": "one", "paths": []}, key, "test-key")
        payload = base64.b64decode(envelope["payload"])
        key.public_key().verify(base64.b64decode(envelope["signature"]), payload)
        self.assertEqual(json.loads(payload)["version"], "one")

    def test_policy_store_hashes_exact_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = gzip.compress(b'{"schema_version":"re8ch.geodb/v1"}')
            (root / "geodb.json.gz").write_bytes(artifact)
            (root / "paths.json").write_text('{"paths":[]}')
            private = Ed25519PrivateKey.generate()
            raw = private.private_bytes_raw()
            (root / "key").write_bytes(base64.b64encode(raw))
            old = (app.GEODB_PATH, app.PATHS_PATH, app.SIGNING_KEY_PATH)
            try:
                app.GEODB_PATH, app.PATHS_PATH, app.SIGNING_KEY_PATH = root / "geodb.json.gz", root / "paths.json", root / "key"
                store = app.PolicyStore()
                envelope = store.manifest()
                payload = json.loads(base64.b64decode(envelope["payload"]))
                self.assertEqual(payload["geoDB"]["byteCount"], len(artifact))
            finally:
                app.GEODB_PATH, app.PATHS_PATH, app.SIGNING_KEY_PATH = old


if __name__ == "__main__":
    unittest.main()
