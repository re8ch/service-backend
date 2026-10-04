import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('download', Path(__file__).with_name('download-oci.py'))
download = importlib.util.module_from_spec(spec)
spec.loader.exec_module(download)


class DownloadTests(unittest.TestCase):
    def test_range_and_size_are_verified(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'part'
            def curl(command, **kwargs):
                p.write_bytes(b'abcd')
                p.with_suffix('.headers').write_text('HTTP/2 206\nContent-Range: bytes 4-7/8\n')
                self.assertNotIn('signed-private-url', ' '.join(command))
                self.assertIn(b'signed-private-url', kwargs['input'])
                return subprocess.CompletedProcess(command, 0)
            with patch.object(download.subprocess, 'run', side_effect=curl):
                download.download_part('https://example.org/signed-private-url', 4, 7, p)
                with self.assertRaisesRegex(ValueError, 'different range'):
                    download.download_part('https://example.org/signed-private-url', 0, 3, p)

    def test_exact_bundle_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            archive = root / 'artifact.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                for target in ('api', 'worker'):
                    for suffix in ('.oci.tar', '.sha256', '-build.json'):
                        z.writestr(target + suffix, b'fixture')
            download.extract(archive, root / 'output')
            self.assertEqual(len(list((root / 'output').iterdir())), 6)
            with zipfile.ZipFile(archive, 'a') as z:
                z.writestr('../escape', b'refuse')
            with self.assertRaisesRegex(ValueError, 'Unexpected artifact'):
                download.extract(archive, root / 'bad')
            self.assertFalse((root / 'escape').exists())

    def test_refuses_plaintext_transport(self):
        with self.assertRaises(ValueError):
            download.curl_config('http://example.org/file')


if __name__ == '__main__':
    unittest.main()
