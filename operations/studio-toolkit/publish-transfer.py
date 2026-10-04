"""Copy trusted hosted-build OCI archives to Harbor without a Docker daemon."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tarfile
import shutil


def validate_archive(root, target):
    archive = (root / (target + '.oci.tar')).resolve()
    metadata = json.loads((root / (target + '-build.json')).read_text())
    digest = metadata['containerimage.digest']
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('Invalid build digest')
    expected = (root / (target + '.sha256')).read_text().strip()
    with archive.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    if not re.fullmatch('[0-9a-f]{64}', expected) or expected != checksum:
        raise ValueError('Transferred OCI archive checksum differs from the hosted build')
    return archive, digest


def unpack_archive(archive, layout):
    layout.mkdir()
    with tarfile.open(archive) as tar:
        for member in tar:
            name = member.name.removeprefix('./')
            if member.isdir() and name in ('.', 'blobs', 'blobs/sha256'):
                continue
            if not member.isfile() or not (name in ('index.json', 'oci-layout') or re.fullmatch(r'blobs/sha256/[0-9a-f]{64}', name)):
                raise ValueError('Unexpected OCI archive entry')
            target = layout / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, target.open('xb') as dest:
                shutil.copyfileobj(source, dest)
    return layout


def worker_config_digest(archive, index_digest):
    """Read the tested platform config from the verified OCI index, not exporter metadata."""
    with tarfile.open(archive) as tar:
        def read_blob(digest):
            if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
                raise ValueError('Invalid OCI digest')
            member = tar.getmember('blobs/sha256/' + digest.split(':')[1])
            if not member.isfile():
                raise ValueError('OCI blob must be a file')
            with tar.extractfile(member) as stream:
                data = stream.read()
            if 'sha256:' + hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('OCI blob checksum differs')
            return json.loads(data)
        index = read_blob(index_digest)
        candidates = [m for m in index['manifests'] if m.get('platform', {}).get('os') == 'linux' and m.get('platform', {}).get('architecture') == 'amd64']
        if len(candidates) != 1:
            raise ValueError('Expected exactly one Linux amd64 image')
        manifest = read_blob(candidates[0]['digest'])
        digest = manifest['config']['digest']
        config = read_blob(digest)
        if (config.get('os'), config.get('architecture')) != ('linux', 'amd64'):
            raise ValueError('OCI config platform differs')
        return digest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--transfer', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--source-sha', required=True)
    args = p.parse_args()
    if not re.fullmatch('[0-9a-f]{40}', args.source_sha):
        p.error('A full source SHA is required')
    archives = {target: validate_archive(args.transfer, target) for target in ['api', 'worker']}
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='studio-publish-auth-') as temp:
        root = Path(temp)
        auth = base64.b64encode((os.environ['HARBOR_USERNAME'] + ':' + os.environ['HARBOR_PASSWORD']).encode()).decode()
        config = root / 'config.json'
        config.write_text(json.dumps({'auths': {'registry.re8ch.com': {'auth': auth}}}))
        config.chmod(0o600)
        layouts = {target: unpack_archive(archive, root / target) for target, (archive, _) in archives.items()}
        # Only these verified local CI layouts are admitted by this process policy.
        policy = root / 'policy.json'
        policy.write_text(json.dumps({'default': [{'type': 'reject'}], 'transports': {'oci': {str(layout): [{'type': 'insecureAcceptAnything'}] for layout in layouts.values()}}}))
        env = dict(os.environ, DOCKER_CONFIG=str(root))
        # All registry and signing requests share the scoped R640 egress transport.
        if os.environ.get('STUDIO_PUBLISH_PROXY'):
            env['HTTPS_PROXY'] = os.environ['STUDIO_PUBLISH_PROXY']
            env['NO_PROXY'] = 'localhost,127.0.0.1'
        for target, (archive, expected) in archives.items():
            image = 'registry.re8ch.com/studio-toolkit/' + target
            digestfile = args.output / (target + '-digest.txt')
            subprocess.run(['skopeo', '--policy', str(policy), 'copy', '--all', '--preserve-digests', '--retry-times', '2', '--dest-authfile', str(config), '--digestfile', str(digestfile), 'oci:' + str(layouts[target]), 'docker://' + image + ':' + args.source_sha], env=env, check=True, timeout=900)
            if digestfile.read_text().strip() != expected:
                raise RuntimeError('Published manifest differs from the tested OCI build')
            (args.output / (target + '-build.json')).write_bytes((args.transfer / (target + '-build.json')).read_bytes())
            subprocess.run(['cosign', 'sign', '--yes', image + '@' + expected], env=env, check=True, timeout=180)
            with (args.output / (target + '-signature.json')).open('wb') as result:
                subprocess.run(['cosign', 'verify', '--certificate-identity', 'https://github.com/re8ch/service-backend/.github/workflows/studio-toolkit-release.yml@refs/heads/main', '--certificate-oidc-issuer', 'https://token.actions.githubusercontent.com', image + '@' + expected], stdout=result, env=env, check=True, timeout=120)
    print('Both immutable hosted-build images copied and signatures verified')


if __name__ == '__main__':
    main()
