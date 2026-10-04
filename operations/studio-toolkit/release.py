"""Harbor project, scan and encrypted reader setup for Studio images built in CI."""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import yaml

PROJECT = 'studio-toolkit'
REGISTRY = 'registry.re8ch.com'


def api(method, path, payload=None):
    auth = base64.b64encode((os.environ['HARBOR_USERNAME'] + ':' + os.environ['HARBOR_PASSWORD']).encode()).decode()
    request = urllib.request.Request('https://' + REGISTRY + '/api/v2.0/' + path, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization': 'Basic ' + auth, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            return response.status, json.loads(body) if body else None
    except urllib.error.HTTPError as error:
        if error.code in (404, 409):
            return error.code, None
        raise RuntimeError(f'Harbor operation failed: HTTP {error.code}') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'scan', 'reader'])
    parser.add_argument('--image', choices=['api', 'worker'])
    parser.add_argument('--digest')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        status, project = api('GET', 'projects/' + PROJECT)
        if status == 404:
            api('POST', 'projects', {'project_name': PROJECT, 'metadata': {'public': 'false', 'auto_scan': 'true'}})
            _, project = api('GET', 'projects/' + PROJECT)
        if project['metadata'].get('public') != 'false':
            raise RuntimeError('Refusing to publish proprietary runtime source to a public project')
        print('Private Studio project ready')
    elif args.action == 'scan':
        if not args.image or not args.digest or not args.digest.startswith('sha256:'):
            raise ValueError('Image name and immutable digest are required')
        path = f'projects/{PROJECT}/repositories/{args.image}/artifacts/' + urllib.parse.quote(args.digest, safe='')
        api('POST', path + '/scan')
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            _, artifact = api('GET', path + '?with_scan_overview=true')
            scans = list(artifact.get('scan_overview', {}).values())
            if scans and all(s.get('scan_status') == 'Success' for s in scans):
                args.output.mkdir(parents=True, exist_ok=True)
                (args.output / (args.image + '-scan.json')).write_text(json.dumps(artifact['scan_overview'], indent=2))
                print(args.image + ': scan completed; review the attached severity report before activation')
                return
            if any(s.get('scan_status') in ('Error', 'Stopped') for s in scans):
                raise RuntimeError('Harbor image scan failed')
            time.sleep(10)
        raise TimeoutError('Harbor image scan did not complete')
    else:
        args.output.mkdir(parents=True, exist_ok=True)
        name = 'studio-runtime-reader-' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ.get('GITHUB_RUN_ATTEMPT', '1')
        _, robot = api('POST', 'robots', {'name': name, 'description': 'Studio runtime pull-only credential', 'duration': -1, 'level': 'project', 'permissions': [{'kind': 'project', 'namespace': PROJECT, 'access': [{'resource': 'repository', 'action': 'pull'}]}]})
        config = {'auths': {REGISTRY: {'auth': base64.b64encode((robot['name'] + ':' + robot['secret']).encode()).decode()}}}
        outputs = {}
        try:
            for namespace in ['studio-toolkit', 'supabase-pg-re8ch']:
                secret = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'studio-registry-reader', 'namespace': namespace}, 'type': 'kubernetes.io/dockerconfigjson', 'stringData': {'.dockerconfigjson': json.dumps(config)}}
                filename = 'studio-registry-reader-' + namespace + '.sops.yaml'
                outputs[filename] = subprocess.check_output(['sops', 'encrypt', '--input-type', 'yaml', '--output-type', 'yaml', '--filename-override', 'clusters/re8ch/secrets/' + filename, '--encrypted-regex', '^(data|stringData)$', '/dev/stdin'], input=yaml.safe_dump(secret).encode())
        except subprocess.CalledProcessError:
            api('DELETE', 'robots/' + str(robot['id']))
            raise
        for filename, encrypted in outputs.items():
            (args.output / filename).write_bytes(encrypted)
        (args.output / 'reader-reference.json').write_text(json.dumps({'id': robot['id'], 'name': robot['name']}))
        print('Encrypted pull-only reader artifact created')


if __name__ == '__main__':
    main()
