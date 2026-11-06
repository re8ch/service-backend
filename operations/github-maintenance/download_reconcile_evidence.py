"""Read the newest artifact for each release name across workflow reruns."""
import argparse
import fnmatch
import io
import json
from pathlib import Path
import subprocess
import zipfile


def latest_artifacts(pages):
    latest = {}
    for page in pages:
        for artifact in page['artifacts']:
            name = artifact['name']
            if not any(fnmatch.fnmatch(name, p) for p in ('*reconcile*', '*release-result*')):
                continue
            if name not in latest or artifact['created_at'] > latest[name]['created_at']:
                latest[name] = artifact
    return list(latest.values())


def download(repo, run, directory):
    pages = json.loads(subprocess.check_output(['gh', 'api', '--paginate', '--slurp',
        f'repos/{repo}/actions/runs/{run}/artifacts']))
    selected = latest_artifacts(pages)
    if not selected:
        raise ValueError('no release evidence')
    for artifact in selected:
        if artifact.get('expired'):
            raise ValueError('latest release evidence expired')
        data = subprocess.check_output(['gh', 'api',
            f'repos/{repo}/actions/artifacts/{artifact["id"]}/zip'])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            # Extract only the known report to a generated path, never archive paths.
            names = [n for n in archive.namelist() if Path(n).name == 'result.json']
            if len(names) != 1:
                raise ValueError('latest artifact lacks a unique release report')
            report = archive.read(names[0])
            json.loads(report)
        target = directory / str(artifact['id'])
        target.mkdir(parents=True, exist_ok=True)
        (target / 'result.json').write_bytes(report)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('repo')
    p.add_argument('run')
    p.add_argument('directory', type=Path)
    args = p.parse_args()
    download(args.repo, args.run, args.directory)
