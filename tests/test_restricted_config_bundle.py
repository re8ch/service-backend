import base64
import importlib.util
import io
from pathlib import Path
import tarfile

import pytest

spec = importlib.util.spec_from_file_location('bundle', Path(__file__).parents[1] / 'operations/infrastructure-contract/bundle.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


def archive(entries):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w:gz') as stream:
        for name, kind in entries:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.linkname = '/tmp/escape' if kind == tarfile.SYMTYPE else ''
            info.size = 0
            stream.addfile(info, io.BytesIO())
    return base64.b64encode(result.getvalue()).decode()


@pytest.mark.parametrize('extra', ['../escape', '/tmp/escape', 'unexpected.yaml', 'cluster.yaml'])
def test_unexpected_paths_and_duplicates_are_rejected(extra):
    with pytest.raises(ValueError):
        bundle.decode(archive([(x, tarfile.REGTYPE) for x in bundle.MEMBERS] + [(extra, tarfile.REGTYPE)]))


def test_links_and_incomplete_archives_are_rejected():
    for entries in [[('cluster.yaml', tarfile.SYMTYPE)], [('cluster.yaml', tarfile.REGTYPE)]]:
        with pytest.raises(ValueError):
            bundle.decode(archive(entries))


def test_mismatched_version_never_writes(tmp_path):
    output = tmp_path / 'private'
    with pytest.raises(ValueError):
        bundle.materialize(archive([(x, tarfile.REGTYPE) for x in bundle.MEMBERS]), output, 'sha256:wrong')
    assert not output.exists()


def test_digest_matches_compiler_convention():
    import hashlib
    files = {name: name.encode() for name in bundle.MEMBERS}
    expected = hashlib.sha256(b''.join(name.encode() + b'\0' + files[name] + b'\0' for name in bundle.MEMBERS)).hexdigest()
    assert bundle.digest(files) == 'sha256:' + expected


def test_valid_bundle_renders_privately_and_matches_pin(tmp_path):
    from test_infrastructure_contracts import synthetic_contracts
    source = tmp_path / 'source'
    synthetic_contracts(source)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w:gz') as stream:
        for name in bundle.MEMBERS:
            stream.add(source / name, arcname=name)
    files = {name: (source / name).read_bytes() for name in bundle.MEMBERS}
    output = tmp_path / 'private'
    bundle.materialize(base64.b64encode(raw.getvalue()).decode(), output, bundle.digest(files))
    assert output.stat().st_mode & 0o777 == 0o700
    for name in bundle.MEMBERS:
        assert (output / name).read_bytes() == files[name]
        assert (output / name).stat().st_mode & 0o777 == 0o600
