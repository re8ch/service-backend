import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'operations/ci/prepare_event_diff.sh'


def git(root, *args):
    return subprocess.check_output(['git', '-c', 'commit.gpgsign=false', *args], cwd=root, text=True).strip()


def history(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    git(source, 'init', '-b', 'main')
    git(source, 'config', 'user.name', 'Fixture')
    git(source, 'config', 'user.email', 'fixture@example.invalid')
    (source / 'base').write_text('base')
    git(source, 'add', '.')
    git(source, 'commit', '-m', 'event base')
    base = git(source, 'rev-parse', 'HEAD')
    git(source, 'checkout', '-b', 'feature')
    (source / 'feature').write_text('feature')
    git(source, 'add', '.')
    git(source, 'commit', '-m', 'feature')
    git(source, 'checkout', 'main')
    for i in range(20):
        (source / 'base').write_text(str(i))
        git(source, 'commit', '-am', f'advance {i}')
    git(source, 'merge', '--no-ff', 'feature', '-m', 'event merge')
    head = git(source, 'rev-parse', 'HEAD')
    clone = tmp_path / 'clone'
    git(tmp_path, 'clone', '--depth=2', source.as_uri(), str(clone))
    return clone, base, head


def test_missing_event_base_is_fetched_without_changing_comparison(tmp_path):
    clone, base, head = history(tmp_path)
    assert subprocess.run(['git', 'merge-base', base, head], cwd=clone, capture_output=True).returncode != 0
    subprocess.run(['bash', str(SCRIPT), base, head], cwd=clone, check=True)
    assert git(clone, 'merge-base', base, head) == base
    assert git(clone, 'diff', '--name-only', f'{base}...{head}').splitlines() == ['base', 'feature']
    assert git(clone, 'rev-parse', 'HEAD') == head


def test_available_history_never_contacts_remote(tmp_path):
    clone, _, head = history(tmp_path)
    parent = git(clone, 'rev-parse', 'HEAD^1')
    git(clone, 'remote', 'set-url', 'origin', '/does-not-exist')
    subprocess.run(['bash', str(SCRIPT), parent, head], cwd=clone, check=True)


@pytest.mark.parametrize('bad_head', ['HEAD', '0' * 40])
def test_untrusted_ref_or_wrong_checkout_is_rejected(tmp_path, bad_head):
    clone, base, _ = history(tmp_path)
    result = subprocess.run(['bash', str(SCRIPT), base, bad_head], cwd=clone, capture_output=True)
    assert result.returncode != 0
