"""Require the Docker acceptance image to match the transferred OCI worker config."""
import argparse
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('transfer', Path(__file__).with_name('publish-transfer.py'))
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--transfer', type=Path, required=True)
p.add_argument('--config-digest', required=True)
a = p.parse_args()
archive, digest = transfer.validate_archive(a.transfer, 'worker')
expected = transfer.worker_config_digest(archive, digest)
if a.config_digest != expected:
    p.error('Acceptance image config differs from the verified OCI worker archive')
print('Acceptance image matches OCI worker config ' + expected)
