import base64
import importlib.util
import os
from pathlib import Path
import struct

import pytest

spec = importlib.util.spec_from_file_location("enrollment", Path(__file__).parents[1] / "operations/host-ops/enroll-management-ssh-key.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
KEY = "ssh-ed25519 " + base64.b64encode(struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + b"x" * 32).decode()


def test_preserves_keys_and_is_idempotent(tmp_path):
    path = tmp_path / ".ssh/authorized_keys"
    path.parent.mkdir()
    old = "# existing access\nssh-ed25519 OTHER existing\n"
    path.write_text(old)
    key, fingerprint = module.public_key(KEY + " comment")
    assert fingerprint.startswith("SHA256:")
    assert module.add_key(path, key, os.getuid(), os.getgid())
    assert path.read_text().startswith(old)
    assert not module.add_key(path, key, os.getuid(), os.getgid())
    assert path.read_text().count(KEY) == 1
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("value", ["private key", "restrict " + KEY, KEY + "\n" + KEY, "ssh-ed25519 YQ=="])
def test_rejects_non_public_or_malformed_key(value):
    with pytest.raises(ValueError):
        module.public_key(value)


def test_refuses_symlink_target(tmp_path):
    target = tmp_path / "preserve"
    target.write_text("original")
    directory = tmp_path / ".ssh"
    directory.mkdir()
    path = directory / "authorized_keys"
    path.symlink_to(target)
    with pytest.raises(ValueError):
        module.add_key(path, KEY, os.getuid(), os.getgid())
    assert target.read_text() == "original"
