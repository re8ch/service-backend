import copy
import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('runner_policy', ROOT / 'tests/check_no_github_hosted_runners.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
PATH = Path('.github/workflows/private-operations-docs.yml')


def test_private_document_runner_exception_requires_every_boundary():
    document = yaml.safe_load((ROOT / PATH).read_text())
    job = document['jobs']['verify']
    assert policy.isolated_private_docs(PATH, 'verify', document, job)
    assert not policy.isolated_private_docs(Path('.github/workflows/ci.yml'), 'verify', document, job)
    assert not policy.isolated_private_docs(PATH, 'other', document, job)
    for key, value in [('if', None), ('environment', None), ('runs-on', 'windows-latest'),
                       ('permissions', {'contents': 'write'})]:
        changed = dict(job, **{key: value})
        assert not policy.isolated_private_docs(PATH, 'verify', document, changed)
    changed = copy.deepcopy(document)
    changed[True]['pull_request'] = {}
    assert not policy.isolated_private_docs(PATH, 'verify', changed, job)
