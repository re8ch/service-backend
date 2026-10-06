#!/usr/bin/env python3
"""Migrate only pg-re8ch Patroni advertisements to inventory-owned AdvFab IPs.

Run on the named host. Dry-run is the default. Configuration backups stay on
the host with mode 0600; configuration values and credentials are never logged.
"""
import argparse
import datetime
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile

import yaml

MEMBERS = {"restricted-node-6dc3f140f9": ("pg-re8ch-1", "128"),
           "domain-ex": ("pg-re8ch-2", "186"), "zan": ("pg-re8ch-3", "16")}


def transform(data, node, inventory):
    member, suffix = MEMBERS[node]
    address = next(n["acceleratedIp"] for n in inventory["nodes"] if n["name"] == node)
    if ipaddress.ip_address(address) not in ipaddress.ip_network("192.0.2.65/24"):
        raise ValueError("address outside declared accelerated domain")
    if data.get("scope") != "pg-re8ch" or data.get("name") != member:
        raise ValueError("Patroni scope/member mismatch")
    old = "10.181.22." + suffix
    pg, api = data["postgresql"], data["restapi"]
    for section, port in [(pg, 5432), (api, 8008)]:
        if section.get("connect_address") not in [f"{old}:{port}", f"{address}:{port}"]:
            raise ValueError("unexpected current advertisement")
        section["connect_address"] = f"{address}:{port}"
    # Bind the single AdvFab identity, preserving REST authentication and ACLs.
    if api.get("listen") not in [f"{old}:8008", f"{address}:8008", "192.0.2.169:8008"]:
        raise ValueError("unexpected REST listener")
    api["listen"] = f"{address}:8008"
    acl = api.get("allowlist", [])
    replacements = {"10.181.22." + s: "10.250.0." + s for _, s in MEMBERS.values()}
    replacements.update({"192.0.2.192": "192.0.2.223", "192.0.2.169": "192.0.2.179"})
    api["allowlist"] = list(dict.fromkeys(replacements.get(item, item) for item in acl))
    return address


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", choices=MEMBERS, required=True)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    path = Path("/etc/patroni/config.yml" if args.node == "zan" else "/etc/patroni/patroni.yml")
    original = path.read_bytes()
    data = yaml.safe_load(original)
    address = transform(data, args.node, json.loads(Path(args.inventory).read_text()))
    # A missing local identity must fail before changing config.
    with socket.socket() as probe:
        probe.bind((address, 0))
    updated = yaml.safe_dump(data, sort_keys=False).encode()
    changed = yaml.safe_load(original) != data
    if args.apply and changed:
        if os.geteuid() != 0:
            raise PermissionError("root required")
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = Path("/var/backups/re8ch-patroni")
        backup.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(backup, 0o700)
        fd = os.open(backup / (path.name + "." + stamp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(original)
        stat = path.stat()
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".advfab-")
        try:
            with os.fdopen(fd, "wb") as handle:
                os.fchmod(handle.fileno(), stat.st_mode & 0o777)
                os.fchown(handle.fileno(), stat.st_uid, stat.st_gid)
                handle.write(updated)
                handle.flush()
                os.fsync(handle.fileno())
            if path.read_bytes() != original:
                raise RuntimeError("configuration changed concurrently")
            os.replace(temporary, path)
            subprocess.run(["systemctl", "reload", "patroni"], check=True, capture_output=True)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    print(json.dumps({"node": args.node, "address": address, "changed": changed,
                      "applied": args.apply, "postgresRestarted": False}))


if __name__ == "__main__":
    main()
