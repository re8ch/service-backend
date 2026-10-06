#!/usr/bin/env python3
"""Enroll the server-held Cloud Platform public key on one reviewed Linux node.

Run as root on the target, providing only the public key on stdin. This does not
install sudo rules, rotate keys, modify sshd policy, or grant root access.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import struct
import subprocess
import sys


def public_key(text):
    lines = text.strip().splitlines()
    if len(lines) != 1:
        raise ValueError("exactly one public key is required")
    parts = lines[0].split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise ValueError("an Ed25519 public key without authorized-key options is required")
    blob = base64.b64decode(parts[1], validate=True)
    if len(blob) != 51 or blob[:19] != struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32):
        raise ValueError("invalid Ed25519 public key")
    return parts[0] + " " + parts[1], "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def add_key(path, key, uid, gid):
    # Fail closed on symlinked paths. Keep any existing enrollment intact.
    if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.is_symlink():
        raise ValueError("refusing symlinked SSH paths")
    path.parent.mkdir(mode=0o700, exist_ok=True)
    os.chown(path.parent, uid, gid)
    path.parent.chmod(0o700)
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
    with os.fdopen(os.open(path, flags, 0o600), "r+", encoding="utf-8") as stream:
        existing = stream.read()
        material = key.split()[1]
        found = any(material in line.split() for line in existing.splitlines() if not line.lstrip().startswith("#"))
        if not found:
            stream.write(("\n" if existing and not existing.endswith("\n") else "") + "restrict " + key + " re8ch-cloud-platform\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.fchown(stream.fileno(), uid, gid)
        os.fchmod(stream.fileno(), 0o600)
    return not found


def main():
    key, fingerprint = public_key(sys.stdin.read())
    if os.geteuid() != 0:
        raise PermissionError("run as root on the reviewed target node")
    account = "re8ch-ai"
    try:
        user = pwd.getpwnam(account)
    except KeyError:
        subprocess.run(["useradd", "--create-home", "--shell", "/bin/bash", account], check=True)
        user = pwd.getpwnam(account)
    if user.pw_dir != "/home/re8ch-ai" or user.pw_shell not in {"/bin/bash", "/bin/sh", "/bin/dash"}:
        raise ValueError("existing management account requires manual review")
    changed = add_key(Path(user.pw_dir) / ".ssh/authorized_keys", key, user.pw_uid, user.pw_gid)
    print(json.dumps({"account": account, "fingerprint": fingerprint, "keyAdded": changed}))


if __name__ == "__main__":
    main()
