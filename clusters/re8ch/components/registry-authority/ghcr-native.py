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
import sys
import tempfile
from urllib.parse import urlsplit

HOSTS_DIR = "/var/lib/rancher/k3s/agent/etc/containerd/certs.d"
REGISTRIES = "/etc/rancher/k3s/registries.yaml"
CONFIG = "/var/lib/rancher/k3s/agent/etc/containerd/config.toml"


def without_ghcr_mirror(raw):
    lines = raw.splitlines(keepends=True)
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
    last = next((i for i in range(first + 1, end) if re.match(r"^  \S", lines[i])), end)
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
    kept, dropping, server_seen = [], False, False
    for line in raw.splitlines(keepends=True):
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
            else:
                dropping = False
                if heading == "rewrite":
                    raise ValueError("canonical host must not rewrite repository identity")
        if dropping:
            continue
        server = re.match(r'''^\s*server\s*=\s*(["'])(.*?)\1\s*(?:#.*)?$''', line.rstrip())
        if server:
            if server_seen or not canonical_endpoint(server.group(2)):
                raise ValueError("unexpected canonical server")
            server_seen = True
        kept.append(line)
    if not server_seen:
        kept.insert(0, 'server = "https://ghcr.io"\n')
    return "".join(kept)


def read_file(path):
    if path.is_symlink():
        raise ValueError("refusing a symlink target")
    return path.read_bytes() if path.exists() else None


def atomic_write(path, data, before):
    if read_file(path) != before:
        raise ValueError("target changed after check")
    path.parent.mkdir(parents=True, exist_ok=True)
    if before is not None:
        backup = path.with_name(path.name + ".pre-re8ch-ghcr-native")
        if backup.is_symlink():
            raise ValueError("refusing a symlink backup")
        try:
            descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(before)
                stream.flush()
                os.fsync(stream.fileno())
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def activate(root, fallback, check=False, expected=None):
    config = root / CONFIG.lstrip("/")
    config_lines = config.read_text().splitlines()
    paths = [match.group(2) for line in config_lines
             if (match := re.match(r'''^\s*config_path\s*=\s*(["'])(.*?)\1''', line))]
    if paths != [HOSTS_DIR]:
        raise ValueError("active hosts-dir is not the reviewed K3s path")
    registry = root / REGISTRIES.lstrip("/")
    hosts = root / HOSTS_DIR.lstrip("/") / "ghcr.io" / "hosts.toml"
    before_registry, before_hosts = read_file(registry), read_file(hosts)
    if before_registry is None and not fallback.strip():
        raise ValueError("missing durable configuration requires the reviewed bootstrap prefix")
    registry_source = before_registry if before_registry is not None else fallback
    targets = [(registry, before_registry, without_ghcr_mirror(registry_source.decode()).encode()),
               (hosts, before_hosts, canonical_hosts((before_hosts or b"").decode()).encode())]
    material = [{"path": str(path.relative_to(root)),
                 "before": hashlib.sha256(before).hexdigest() if before is not None else None,
                 "after": hashlib.sha256(after).hexdigest()} for path, before, after in targets]
    node = os.environ.get("NODE_NAME", "")
    fingerprint = hashlib.sha256(json.dumps({"node": node, "files": material}, sort_keys=True).encode()).hexdigest()
    if expected is not None and expected != fingerprint:
        raise ValueError("checked state changed; run check again")
    # Validate every target before the first write. Keep opaque auth bytes intact.
    for path, before, _ in targets:
        if read_file(path) != before:
            raise ValueError("target changed while checking")
    if not check:
        for path, before, after in targets:
            if before != after:
                atomic_write(path, after, before)
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
        result = activate(args.root, sys.stdin.buffer.read(), args.check, args.expected_state)
    except (OSError, UnicodeError, ValueError) as error:
        # Exception text can contain source snippets; never log it or auth data.
        print(json.dumps({"registry": "ghcr.io", "errorClass": type(error).__name__, "applied": False}), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(result, sort_keys=True))
