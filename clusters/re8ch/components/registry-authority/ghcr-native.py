#!/usr/bin/env python3
"""Activate canonical GHCR in the existing K3s hosts-dir, without service restart.

Registry authentication is opaque: never decode, serialize or print configs or
host headers. Only the GHCR mirror block and its reviewed Harbor host are removed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from urllib.parse import urlsplit

HOSTS_DIR = "/var/lib/rancher/k3s/agent/etc/containerd/certs.d"
REGISTRIES = "/etc/rancher/k3s/registries.yaml"
CONFIG = "/var/lib/rancher/k3s/agent/etc/containerd/config.toml"


def without_ghcr_mirror(raw):
    lines = raw.splitlines(keepends=True)
    if any(line and not line[0].isspace() and not line.startswith("#")
           and not re.match(r"^(mirrors|configs):", line) for line in lines):
        raise ValueError("unsupported registry top-level structure")
    if any(re.match(r'''^["']mirrors["']:''', line) for line in lines):
        raise ValueError("unsupported quoted mirrors key")
    starts = [i for i, line in enumerate(lines) if re.match(r"^mirrors:\s*(?:#.*)?$", line.rstrip())]
    if len(starts) > 1:
        raise ValueError("duplicate mirrors mapping")
    if not starts:
        if any(re.match(r"^mirrors:", line) and not re.match(r"^mirrors:\s*\{\s*\}\s*(?:#.*)?$", line.rstrip()) for line in lines):
            raise ValueError("unsupported inline mirrors mapping")
        return raw
    start = starts[0]
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^[^\s#][^:]*:", lines[i])), len(lines))
    if any(re.match(r'''^\s+(?:\*|'\*'|"\*"|<<|'<<'|"<<"):\s*''', line)
           for line in lines[start + 1:end]):
        raise ValueError("inherited or wildcard mirrors require separate review")
    if any(re.match(r'''^  ["'][^"']*\\''', line) for line in lines[start + 1:end]):
        raise ValueError("escaped mirror keys require separate review")
    if any(re.match(r'''^\s+(?:ghcr\.io|'ghcr\.io'|"ghcr\.io"):''', line)
           and not line.startswith("  ghcr.io:") and not line.startswith("  'ghcr.io':")
           and not line.startswith('  "ghcr.io":') for line in lines[start + 1:end]):
        raise ValueError("unsupported GHCR mirror indentation")
    matches = [i for i in range(start + 1, end) if re.match(r"^  (?:ghcr\.io|'ghcr\.io'|\"ghcr\.io\"):", lines[i])]
    if len(matches) > 1:
        raise ValueError("duplicate GHCR mirror")
    if not matches:
        return raw
    first = matches[0]
    last = next((i for i in range(first + 1, end) if re.match(r"^  [^\s#][^:]*:", lines[i])), end)
    if any(re.search(r"(?:^|[\s{])&[A-Za-z_]", line) for line in lines[first:last]):
        raise ValueError("GHCR mirror owns an unsupported YAML anchor")
    remaining = lines[start + 1:first] + lines[last:end]
    if not any(line.strip() and not line.lstrip().startswith("#") for line in remaining):
        newline = "\r\n" if lines[start].endswith("\r\n") else "\n"
        return "".join(lines[:start] + ["mirrors: {}" + newline] + remaining + lines[end:])
    return "".join(lines[:first] + lines[last:])


def canonical_endpoint(value):
    parsed = urlsplit(value)
    return (parsed.scheme == "https" and parsed.hostname == "ghcr.io"
            and parsed.port in (None, 443) and parsed.path in ("", "/", "/v2", "/v2/")
            and not (parsed.username or parsed.password or parsed.query or parsed.fragment))


def canonical_hosts(raw):
    kept, dropping, server_seen, context = [], False, False, "root"
    allowed = {"server", "capabilities", "ca", "client", "skip_verify", "override_path", "header", "dial_timeout"}
    for line in raw.splitlines(keepends=True):
        if line.lstrip().startswith("#") or not line.strip():
            if not dropping:
                kept.append(line)
            continue
        if '\"\"\"' in line or "'''" in line:
            raise ValueError("multiline TOML values require separate review")
        section = re.match(r'^\s*\[(.+)\]\s*(?:#.*)?$', line.rstrip())
        if section:
            heading = section.group(1)
            host = re.fullmatch(r'''host\.(["'])(.+)\1(?:\.[A-Za-z_][\w-]*)*''', heading)
            if heading.startswith("host.") and not host:
                raise ValueError("unrecognized host table")
            if host:
                value = host.group(2)
                parsed = urlsplit(value)
                harbor = (parsed.scheme == "https" and parsed.hostname == "registry.re8ch.com"
                          and parsed.port in (None, 443) and parsed.path in ("", "/v2", "/v2/", "/v2/proxy-ghcr")
                          and not (parsed.username or parsed.password or parsed.query or parsed.fragment))
                if not harbor and not canonical_endpoint(value):
                    raise ValueError("unreviewed GHCR host endpoint")
                if not harbor and heading.endswith(".rewrite"):
                    raise ValueError("canonical host must not rewrite repository identity")
                dropping = harbor
                suffix = heading[heading.rfind(host.group(1)) + 1:]
                if suffix not in ("", ".header", ".rewrite"):
                    raise ValueError("unknown host subsection")
                context = "header" if suffix == ".header" else "host"
            else:
                dropping = False
                if heading not in ("header", "host"):
                    raise ValueError("unknown TOML table")
                context = "header" if heading == "header" else "empty-host"
        if dropping:
            continue
        if not section:
            if context == "empty-host":
                raise ValueError("inline host mappings are not permitted")
            if context != "header" and "=" in line:
                assignment = re.match(r'''^\s*(?:"([^"\\]+)"|'([^']+)'|([A-Za-z0-9_-]+))\s*=''', line)
                key = next((v for v in assignment.groups() if v is not None), None) if assignment else None
                if key not in allowed or (context == "host" and key == "server"):
                    raise ValueError("unknown or inline TOML host structure")
        server = re.match(r'''^\s*(?:server|"server"|'server')\s*=\s*(["'])(.*?)\1\s*(?:#.*)?$''', line.rstrip())
        if not section and context == "root" and "=" in line and key == "server" and not server:
            raise ValueError("canonical server must be an explicit string")
        if server and context == "root":
            if server_seen or not canonical_endpoint(server.group(2)):
                raise ValueError("unexpected canonical server")
            server_seen = True
        kept.append(line)
    if not server_seen:
        kept.insert(0, 'server = "https://ghcr.io"\n')
    return "".join(kept)


def scoped_path(root, path):
    relative = path.relative_to(root)
    cursor = root
    for part in (None, *relative.parts):
        if part is not None:
            cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("refusing a symlink path component")


def read_file(root, path):
    scoped_path(root, path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("expected a regular file")
        return stream.read()


def private_backup(root, path):
    scoped_path(root, path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("existing backup is not a private regular file")
    return read_file(root, path)


def write_bytes(stream, data):
    remaining = memoryview(data)
    while remaining:
        written = stream.write(remaining)
        if written is None or written <= 0:
            raise OSError("incomplete file write")
        remaining = remaining[written:]
    stream.flush()
    os.fsync(stream.fileno())


def complete_temp(root, path, data):
    scoped_path(root, path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            write_bytes(stream, data)
        return temporary
    except BaseException:
        os.unlink(temporary)
        raise


def sync_directory(root, directory):
    scoped_path(root, directory)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(root, path, data, before):
    if read_file(root, path) != before:
        raise ValueError("target changed after check")
    path.parent.mkdir(parents=True, exist_ok=True)
    if before is not None:
        backup = path.with_name(path.name + ".pre-re8ch-ghcr-native")
        scoped_path(root, backup)
        temporary = complete_temp(root, backup, before)
        try:
            try:
                os.link(temporary, backup)
            except FileExistsError:
                if private_backup(root, backup) != before:
                    raise ValueError("existing backup does not match the original")
        finally:
            os.unlink(temporary)
        sync_directory(root, path.parent)
    temporary = complete_temp(root, path, data)
    try:
        if read_file(root, path) != before:
            raise ValueError("target changed before replacement")
        os.replace(temporary, path)
        sync_directory(root, path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def activate(root, check=False, expected=None):
    root = Path(os.path.abspath(root))
    config = root / CONFIG.lstrip("/")
    config_bytes = read_file(root, config)
    if config_bytes is None:
        raise ValueError("active containerd configuration is missing")
    config_lines = config_bytes.decode().splitlines()
    paths = [match.group(2) for line in config_lines
             if (match := re.match(r'''^\s*config_path\s*=\s*(["'])(.*?)\1''', line))]
    if paths != [HOSTS_DIR]:
        raise ValueError("active hosts-dir is not the reviewed K3s path")
    registry = root / REGISTRIES.lstrip("/")
    hosts = root / HOSTS_DIR.lstrip("/") / "ghcr.io" / "hosts.toml"
    before_registry, before_hosts = read_file(root, registry), read_file(root, hosts)
    if before_registry is None or before_hosts is None:
        raise ValueError("existing durable and active GHCR files are required")
    targets = [(registry, before_registry, without_ghcr_mirror(before_registry.decode()).encode()),
               (hosts, before_hosts, canonical_hosts(before_hosts.decode()).encode())]
    material = [{"path": str(path.relative_to(root)),
                 "before": hashlib.sha256(before).hexdigest() if before is not None else None,
                 "after": hashlib.sha256(after).hexdigest()} for path, before, after in targets]
    node = os.environ.get("NODE_NAME", "")
    fingerprint = hashlib.sha256(json.dumps({"node": node, "files": material}, sort_keys=True).encode()).hexdigest()
    if expected is not None and expected != fingerprint:
        raise ValueError("checked state changed; run check again")
    # Validate every target before the first write. Keep opaque auth bytes intact.
    for path, before, after in targets:
        if read_file(root, path) != before:
            raise ValueError("target changed while checking")
        backup = path.with_name(path.name + ".pre-re8ch-ghcr-native")
        backup_data = private_backup(root, backup)
        if before != after and backup_data is not None and backup_data != before:
            raise ValueError("existing backup does not match the original")
    if not check:
        for path, before, after in targets:
            if before != after:
                atomic_write(root, path, after, before)
    return {"registry": "ghcr.io", "node": node, "fingerprint": fingerprint, "checkOnly": check,
            "durableChanged": targets[0][1] != targets[0][2],
            "effectiveChanged": targets[1][1] != targets[1][2], "restarted": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/"))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--expected-state")
    args = parser.parse_args()
    try:
        result = activate(args.root, args.check, args.expected_state)
    except (OSError, UnicodeError, ValueError) as error:
        # Exception text can contain source snippets; never log it or auth data.
        print(json.dumps({"registry": "ghcr.io", "errorClass": type(error).__name__,
                          "completed": False, "state": "read-back-required"}), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(result, sort_keys=True))
