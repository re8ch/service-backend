"""Download a same-run private Actions artifact using bounded parallel HTTPS ranges."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile


def curl_config(url, token=None):
    if not url.startswith('https://') or any(c in url for c in '\r\n'):
        raise ValueError('Expected an HTTPS artifact URL')
    config = 'url = ' + json.dumps(url) + '\n'
    if token:
        config += 'header = "Authorization: Bearer ' + token + '"\n'
    return config.encode()


def download_part(url, start, end, output, proxy=None):
    headers = output.with_suffix('.headers')
    command = ['curl', '--silent', '--show-error', '--fail', '--connect-timeout', '20',
               '--max-time', '1800', '--retry', '2', '--range', f'{start}-{end}',
               '--max-filesize', str(end - start + 1), '--dump-header', str(headers),
               '--output', str(output), '--config', '-']
    env = dict(os.environ)
    for key in ('HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'https_proxy', 'http_proxy', 'all_proxy'):
        env.pop(key, None)
    if proxy:
        command += ['--proxy', proxy]
    result = subprocess.run(command, input=curl_config(url), capture_output=True, env=env, timeout=5500)
    if result.returncode:
        raise RuntimeError('Artifact range transport failed: curl exit ' + str(result.returncode))
    text = headers.read_text()
    if not re.search(r'(?im)^content-range:\s*bytes ' + str(start) + '-' + str(end) + r'/\d+\s*$', text):
        raise ValueError('Artifact server returned a different range')
    if output.stat().st_size != end - start + 1:
        raise ValueError('Artifact range size differs')


def extract(archive, output):
    expected = {target + suffix for target in ('api', 'worker') for suffix in ('.oci.tar', '.sha256', '-build.json')}
    with zipfile.ZipFile(archive) as z:
        entries = z.infolist()
        if len(entries) != len(expected) or {e.filename for e in entries} != expected:
            raise ValueError('Unexpected artifact files')
        if sum(e.file_size for e in entries) > 3 * 1024**3:
            raise ValueError('Artifact exceeds transfer bound')
        output.mkdir()
        for entry in entries:
            with z.open(entry) as source, (output / entry.filename).open('xb') as dest:
                shutil.copyfileobj(source, dest)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', a.repo) or not a.run_id.isdigit():
        p.error('Invalid repository or run ID')
    proxy = os.environ.get('STUDIO_PUBLISH_PROXY')
    env = dict(os.environ)
    if proxy:
        env['HTTPS_PROXY'] = proxy
    artifacts = json.loads(subprocess.check_output(['gh', 'api', f'repos/{a.repo}/actions/runs/{a.run_id}/artifacts'], env=env))['artifacts']
    matches = [x for x in artifacts if x['name'] == 'studio-oci' and not x['expired']]
    if len(matches) != 1:
        raise ValueError('Expected one live same-run studio-oci artifact')
    artifact = matches[0]
    size = artifact['size_in_bytes']
    if not 0 < size <= 3 * 1024**3:
        raise ValueError('Artifact exceeds transfer bound')
    with tempfile.TemporaryDirectory(prefix='studio-artifact-') as temp:
        root = Path(temp)
        headers = root / 'redirect.headers'
        command = ['curl', '--silent', '--show-error', '--max-time', '30', '--dump-header', str(headers), '--output', os.devnull, '--config', '-']
        if proxy:
            command += ['--proxy', proxy]
        result = subprocess.run(command, input=curl_config(f'https://api.github.com/repos/{a.repo}/actions/artifacts/{artifact["id"]}/zip', os.environ['GH_TOKEN']), capture_output=True, timeout=35)
        if result.returncode:
            raise RuntimeError('Could not obtain artifact download URL')
        locations = re.findall(r'(?im)^location:\s*(\S+)\s*$', headers.read_text())
        if len(locations) != 1:
            raise ValueError('Expected one artifact redirect')
        url = locations[0]
        # Small ranges let fast connections keep working instead of waiting for
        # one 90 MiB straggler to hit the per-request timeout and restart.
        chunk = 8 * 1024**2
        parts = [(start, min(size - 1, start + chunk - 1), root / f'part-{n}') for n, start in enumerate(range(0, size, chunk))]
        def fetch(part):
            start, end, path = part
            try:
                download_part(url, start, end, path)
            except (RuntimeError, subprocess.TimeoutExpired):
                if not proxy:
                    raise
                download_part(url, start, end, path, proxy)
            print(f'Artifact range {start}-{end} verified', flush=True)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(fetch, parts))
        archive = root / 'artifact.zip'
        with archive.open('xb') as dest:
            for _, _, part in parts:
                with part.open('rb') as source:
                    shutil.copyfileobj(source, dest)
        if archive.stat().st_size != size:
            raise ValueError('Artifact size differs')
        extract(archive, a.output)
    print('Same-run OCI artifact downloaded and ZIP integrity verified', flush=True)


if __name__ == '__main__':
    main()
