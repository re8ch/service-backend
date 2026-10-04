import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
import yaml

RENDER = Path(__file__).with_name('render-runtime.py')


class RuntimeTests(unittest.TestCase):
    def test_render_is_scoped_and_pinned(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'runtime.yaml'
            images = ['registry.re8ch.com/studio-toolkit/' + target + '@sha256:' + 'a' * 64 for target in ['api', 'worker']]
            subprocess.run([sys.executable, str(RENDER), '--api-image', images[0], '--worker-image', images[1], '--output', str(output)], check=True)
            docs = list(yaml.safe_load_all(output.read_text()))
            deployments = {d['metadata']['name']: d['spec']['template']['spec'] for d in docs if d['kind'] == 'Deployment'}
            self.assertEqual(len(deployments), 4)
            pvc = next(d for d in docs if d['kind'] == 'PersistentVolumeClaim')
            self.assertEqual(pvc['spec']['storageClassName'], 'cephfs-r640')
            for pod in deployments.values():
                self.assertFalse(pod['automountServiceAccountToken'])
                self.assertTrue(pod['securityContext']['runAsNonRoot'])
                for container in pod['containers']:
                    self.assertIn('@sha256:', container['image'])
                    self.assertTrue(container['securityContext']['readOnlyRootFilesystem'])
            ro = {e['name']: e for e in deployments['studio-db-ro']['containers'][0]['env']}
            self.assertEqual(ro['PGRST_DB_URI']['valueFrom']['secretKeyRef']['key'], 'PGRST_DB_URI_RW')
            self.assertEqual(ro['PGRST_DB_TX_END']['value'], 'rollback')
            self.assertEqual(ro['PGRST_DB_CHANNEL_ENABLED']['value'], 'false')
            self.assertEqual(ro['PGOPTIONS']['value'], '-c default_transaction_read_only=on')
            worker = deployments['studio-worker-r640']
            self.assertEqual(worker['nodeSelector'], {'kubernetes.io/hostname': 'r640'})
            self.assertFalse(any('persistentVolumeClaim' in v for v in worker['volumes']))
            api_env = deployments['studio-api']['containers'][0]['env']
            values = {e['name']: e.get('value') for e in api_env}
            self.assertEqual(values['STUDIO_MODE'], 'production')
            self.assertEqual(values['STUDIO_POSTGREST_RO'], 'https://db-ro.re8ch.com/studio')
            self.assertEqual(values['STUDIO_POSTGREST_RW'], 'https://db-rw.re8ch.com/studio')
            routes = [d for d in docs if d['kind'] == 'HTTPRoute']
            self.assertEqual(len(routes), 3)
            for route in routes:
                rule = route['spec']['rules'][0]
                self.assertEqual(rule['matches'][0]['path']['value'], '/studio')
                self.assertEqual(route['spec']['parentRefs'][0]['name'], 're8ch-gateway-canary')
            migration = next(d for d in docs if d['kind'] == 'Job')
            self.assertEqual(migration['metadata']['namespace'], 'supabase-pg-re8ch')
            for container in migration['spec']['template']['spec']['initContainers'] + migration['spec']['template']['spec']['containers']:
                for bound in ['requests', 'limits']:
                    self.assertIn('cpu', container['resources'][bound])
                    self.assertIn('memory', container['resources'][bound])
            self.assertNotIn('Secret', [d['kind'] for d in docs])

    def test_upgrade_preserves_completed_bootstrap(self):
        with tempfile.TemporaryDirectory() as temp:
            old = 'registry.re8ch.com/studio-toolkit/api@sha256:' + 'a' * 64
            new = 'registry.re8ch.com/studio-toolkit/api@sha256:' + 'b' * 64
            worker = 'registry.re8ch.com/studio-toolkit/worker@sha256:' + 'c' * 64
            def render(api, migration=None):
                output = Path(temp) / ('runtime-' + api[-1] + '.yaml')
                cmd = [sys.executable, str(RENDER), '--api-image', api, '--worker-image', worker, '--output', str(output)]
                if migration:
                    cmd += ['--migration-image', migration]
                subprocess.run(cmd, check=True)
                return list(yaml.safe_load_all(output.read_text()))
            before, after = render(old), render(new, old)
            self.assertEqual(next(d for d in before if d['kind'] == 'Job'), next(d for d in after if d['kind'] == 'Job'))
            api = next(d for d in after if d['kind'] == 'Deployment' and d['metadata']['name'] == 'studio-api')
            self.assertEqual(api['spec']['template']['spec']['containers'][0]['image'], new)

    def test_rejects_mutable_or_foreign_images(self):
        result = subprocess.run([sys.executable, str(RENDER), '--api-image', 'registry.re8ch.com/studio-toolkit/api:latest', '--worker-image', 'untrusted/worker:latest', '--output', '/tmp/should-not-render-studio.yaml'], capture_output=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
