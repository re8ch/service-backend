"""Exercise the exact workflow evaluator without GitHub credentials or network."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/github-spec-shadow.yml'


@pytest.mark.parametrize('case,reason', [
    ('valid', None),
    ('closed', 'proposal-not-open'),
    ('other-base', 'target-not-main'),
    ('superseded', 'event-superseded'),
    ('wrong-candidate', 'candidate-mismatch'),
    ('stale', 'not-single-parent-current-main'),
    ('merge', 'not-single-parent-current-main'),
    ('unsigned', 'signature-not-github-verified'),
    ('missing-signature', 'signature-not-github-verified'),
])
def test_workflow_reports_ineligibility_without_granting_authority(tmp_path, case, reason):
    workflow = yaml.safe_load(WORKFLOW.read_text())
    shell = workflow['jobs']['acceptance-shadow']['steps'][0]['run']
    program = shell.split("python - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    base, head = 'a' * 40, 'b' * 40
    proposal = {'state': 'open', 'base': {'ref': 'main'}, 'head': {'sha': head}}
    candidate = {'sha': head, 'parents': [{'sha': base}], 'verification': {'verified': True}}
    if case == 'closed': proposal['state'] = 'closed'
    if case == 'other-base': proposal['base']['ref'] = 'staging'
    if case == 'superseded': proposal['head']['sha'] = 'c' * 40
    if case == 'wrong-candidate': candidate['sha'] = 'c' * 40
    if case == 'stale': candidate['parents'] = [{'sha': 'c' * 40}]
    if case == 'merge': candidate['parents'].append({'sha': 'c' * 40})
    if case == 'unsigned': candidate['verification']['verified'] = False
    if case == 'missing-signature': del candidate['verification']
    for name, value in [('proposal', proposal), ('candidate', candidate),
                        ('authority', {'object': {'sha': base}})]:
        (tmp_path / f'{name}.json').write_text(json.dumps(value))
    result = subprocess.run([sys.executable, '-c', program], check=True, text=True,
        capture_output=True, timeout=10, env=dict(os.environ,
            RUNNER_TEMP=str(tmp_path), EVENT_HEAD=head, PR_NUMBER='580',
            GITHUB_STEP_SUMMARY=str(tmp_path / 'summary.md')))
    report = json.loads(result.stdout)
    assert report['authorization'] is False
    assert report['structuralEligibility'] is (reason is None)
    assert report['reasons'] == ([] if reason is None else [reason])
    assert 'atomic-admission' in report['unverified']
