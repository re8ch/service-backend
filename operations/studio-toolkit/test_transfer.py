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


class OCIPreservationTests(unittest.TestCase):
    @unittest.skipUnless(__import__('shutil').which('skopeo'), 'Skopeo is required for a real OCI copy')
    def test_copy_preserves_index_and_attestation_blobs(self):
        import subprocess
        import tarfile
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            layout = root / 'source'
            blobs = layout / 'blobs' / 'sha256'
            blobs.mkdir(parents=True)
            def blob(value, media):
                data = json.dumps(value, separators=(',', ':')).encode()
                digest = hashlib.sha256(data).hexdigest()
                (blobs / digest).write_bytes(data)
                return {'mediaType': media, 'digest': 'sha256:' + digest, 'size': len(data)}
            cfg = blob({'architecture': 'amd64', 'os': 'linux', 'rootfs': {'type': 'layers', 'diff_ids': []}}, 'application/vnd.oci.image.config.v1+json')
            image = blob({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json', 'config': cfg, 'layers': []}, 'application/vnd.oci.image.manifest.v1+json')
            image['platform'] = {'architecture': 'amd64', 'os': 'linux'}
            statement = blob({'_type': 'https://in-toto.io/Statement/v0.1', 'predicateType': 'https://spdx.dev/Document', 'subject': [], 'predicate': {}}, 'application/vnd.in-toto+json')
            acfg = blob({'architecture': 'unknown', 'os': 'unknown', 'rootfs': {'type': 'layers', 'diff_ids': []}}, 'application/vnd.oci.image.config.v1+json')
            attest = blob({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json', 'config': acfg, 'layers': [statement]}, 'application/vnd.oci.image.manifest.v1+json')
            attest.update(platform={'architecture': 'unknown', 'os': 'unknown'}, annotations={'vnd.docker.reference.type': 'attestation-manifest', 'vnd.docker.reference.digest': image['digest']})
            index = blob({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.index.v1+json', 'manifests': [image, attest]}, 'application/vnd.oci.image.index.v1+json')
            (layout / 'oci-layout').write_text('{"imageLayoutVersion":"1.0.0"}')
            (layout / 'index.json').write_text(json.dumps({'schemaVersion': 2, 'manifests': [index]}))
            archive = root / 'fixture.tar'
            with tarfile.open(archive, 'w') as tar:
                for file in layout.rglob('*'):
                    if file.is_file(): tar.add(file, arcname=str(file.relative_to(layout)))
            policy = root / 'policy.json'
            policy.write_text(json.dumps({'default': [{'type': 'reject'}], 'transports': {'oci-archive': {str(archive): [{'type': 'insecureAcceptAnything'}]}}}))
            digestfile = root / 'digest'
            result = subprocess.run(['skopeo', '--policy', str(policy), 'copy', '--all', '--preserve-digests', '--digestfile', str(digestfile), 'oci-archive:' + str(archive), 'oci:' + str(root / 'copied') + ':test'], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            self.assertEqual(digestfile.read_text().strip(), index['digest'])
            for file in blobs.iterdir():
                self.assertEqual(file.read_bytes(), (root / 'copied' / 'blobs' / 'sha256' / file.name).read_bytes())


if __name__ == '__main__':
    unittest.main()
