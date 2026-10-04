import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml

spec = importlib.util.spec_from_file_location('studio_release', Path(__file__).with_name('release.py'))
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class RegistryTransportTests(unittest.TestCase):
    def test_scoped_proxy_does_not_put_credentials_on_argv(self):
        with patch.dict(os.environ, {'HARBOR_USERNAME': 'test-user', 'HARBOR_PASSWORD': 'fixture-only', 'STUDIO_PUBLISH_PROXY': 'socks5h://127.0.0.1:1234'}), patch.object(release.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'{"ok":true}\n200')) as run:
            self.assertEqual(release.api('GET', 'health'), (200, {'ok': True}))
            command = run.call_args.args[0]
            self.assertIn('socks5h://127.0.0.1:1234', command)
            self.assertNotIn('fixture-only', ' '.join(command))
            self.assertIn(b'Authorization: Basic ', run.call_args.kwargs['input'])
            self.assertNotIn('--insecure', command)

    def test_transport_errors_do_not_expose_response_or_credentials(self):
        with patch.dict(os.environ, {'HARBOR_USERNAME': 'test-user', 'HARBOR_PASSWORD': 'fixture-only', 'STUDIO_PUBLISH_PROXY': 'socks5h://127.0.0.1:1234'}), patch.object(release.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'private response\n403')):
            with self.assertRaisesRegex(RuntimeError, '^Harbor operation failed: HTTP 403$'):
                release.api('GET', 'health')


class ScanEvidenceTests(unittest.TestCase):
    def test_preserves_full_findings_even_when_critical(self):
        overview = {'report': {'scan_status': 'Success', 'severity': 'Critical'}}
        findings = {'report': {'vulnerabilities': [{'id': 'CVE-fixture', 'severity': 'Critical', 'package': 'fixture', 'fix_version': '2'}]}}
        with tempfile.TemporaryDirectory() as root, patch.object(release, 'api', side_effect=[(202, None), (200, {'scan_overview': overview}), (200, findings)]) as api, patch.object(sys, 'argv', ['release', 'scan', '--image', 'worker', '--digest', 'sha256:' + 'a' * 64, '--output', root]):
            release.main()
            self.assertEqual(json.loads((Path(root) / 'worker-vulnerabilities.json').read_text()), findings)
            self.assertTrue(api.call_args.args[1].endswith('/additions/vulnerabilities'))

    def test_missing_report_does_not_pass_as_complete_evidence(self):
        for response in [(404, None), (200, {}), (200, {'report': {'severity': 'Critical'}})]:
            with self.subTest(response=response), tempfile.TemporaryDirectory() as root, patch.object(release, 'api', side_effect=[(202, None), (200, {'scan_overview': {'report': {'scan_status': 'Success'}}}), response]), patch.object(sys, 'argv', ['release', 'scan', '--image', 'api', '--digest', 'sha256:' + 'a' * 64, '--output', root]):
                with self.assertRaisesRegex(RuntimeError, 'complete vulnerability report'):
                    release.main()
                self.assertFalse((Path(root) / 'api-vulnerabilities.json').exists())


class ReaderTests(unittest.TestCase):
    def run_reader(self, output, encrypt):
        robot = {'name': 'robot$studio', 'secret': 'fixture-only', 'id': 27}
        with patch.object(release, 'api', return_value=(201, robot)) as api, \
             patch.object(release.subprocess, 'check_output', side_effect=encrypt), \
             patch.dict(os.environ, {'GITHUB_RUN_ID': 'test'}), \
             patch.object(sys, 'argv', ['release', 'reader', '--output', output]):
            try:
                release.main()
            finally:
                self.calls = api.call_args_list

    def test_encrypts_each_namespace_before_writing(self):
        namespaces = []
        def encrypt(command, input):
            secret = yaml.safe_load(input)
            namespaces.append(secret['metadata']['namespace'])
            self.assertIn('fixture-only', __import__('base64').b64decode(json.loads(secret['stringData']['.dockerconfigjson'])['auths'][release.REGISTRY]['auth']).decode())
            return b'ciphertext-only'
        with tempfile.TemporaryDirectory() as root:
            self.run_reader(root, encrypt)
            self.assertEqual(namespaces, ['studio-toolkit', 'supabase-pg-re8ch'])
            self.assertEqual(len(list(Path(root).glob('*.sops.yaml'))), 2)
            for file in Path(root).iterdir():
                self.assertNotIn('fixture-only', file.read_text())
        permission = self.calls[0].args[2]['permissions'][0]['access']
        self.assertEqual(permission, [{'resource': 'repository', 'action': 'pull'}])

    def test_encryption_failure_revokes_robot_and_leaves_no_files(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(subprocess.CalledProcessError):
                self.run_reader(root, [b'first-ciphertext', subprocess.CalledProcessError(1, 'sops')])
            self.assertEqual(list(Path(root).iterdir()), [])
            self.assertEqual(self.calls[-1].args, ('DELETE', 'robots/27'))


if __name__ == '__main__':
    unittest.main()
