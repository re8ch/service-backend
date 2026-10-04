import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('transfer', Path(__file__).with_name('publish-transfer.py'))
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)


class TransferTests(unittest.TestCase):
    def fixture(self, root):
        ((root / 'api.oci.tar').resolve()).write_bytes(b'fixture OCI bytes')
        (root / 'api.sha256').write_text(hashlib.sha256(b'fixture OCI bytes').hexdigest())
        (root / 'api-build.json').write_text(json.dumps({'containerimage.digest': 'sha256:' + 'a' * 64}))

    def test_accepts_matching_transfer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            archive, digest = transfer.validate_archive(root, 'api')
            self.assertEqual(archive, (root / 'api.oci.tar').resolve())
            self.assertEqual(digest, 'sha256:' + 'a' * 64)

    def test_refuses_modified_archive_before_registry_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            (root / 'api.oci.tar').write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                transfer.validate_archive(root, 'api')

    def test_refuses_mutable_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            (root / 'api-build.json').write_text(json.dumps({'containerimage.digest': 'latest'}))
            with self.assertRaisesRegex(ValueError, 'digest'):
                transfer.validate_archive(root, 'api')


if __name__ == '__main__':
    unittest.main()
