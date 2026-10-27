import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_migrated_operations_documents_are_exact_public_pointers():
    manifest = json.loads((ROOT / 'contracts/private-operations-docs/sources.json').read_text())
    assert manifest['schemaVersion'] == 1
    assert manifest['projectUrl'] == 'https://github.com/orgs/re8ch/projects/2'
    assert len({row['path'] for row in manifest['documents']}) == len(manifest['documents'])
    for row in manifest['documents']:
        path = ROOT / row['path']
        assert path.resolve().is_relative_to(ROOT)
        assert len(row['sha256']) == 64
        int(row['sha256'], 16)
        assert row['itemId'].startswith('PVTI_')
        assert row['draftId'].startswith('DI_')
        expected = ('# Restricted operations document\n\n'
                    'This document is maintained in the private operations Project.\n\n'
                    f"[Read the internal document]({row['url']})\n\n"
                    'Access requires Project authorization. See '
                    '[reading and maintenance instructions]('
                    'https://github.com/re8ch/service-backend/blob/main/contracts/private-operations-docs/README.md).\n')
        assert path.read_text() == expected
        assert hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']
