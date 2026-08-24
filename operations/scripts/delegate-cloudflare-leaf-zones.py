#!/usr/bin/env python3
"""Delegate RE8CH leaf zones to the regional in-cluster authorities.

The caller supplies CF_API_TOKEN. The script snapshots all affected records,
creates glue records, replaces only the three leaf apexes, and restores the
snapshot if any Cloudflare operation fails.
"""

import argparse
import datetime
import json
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request


BASE = "https://api.cloudflare.com/client/v4"
APEXES = ("api.re8ch.com", "db-ro.re8ch.com", "db-rw.re8ch.com")
AUTHORITIES = {
    "ns-sh.re8ch.com": "124.220.77.50",
    "ns-gz.re8ch.com": "42.194.130.91",
    "ns-us.re8ch.com": "38.207.168.50",
}


class Cloudflare:
    def __init__(self, token):
        self.token = token

    def call(self, method, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            BASE + path,
            data=data,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                output = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"{method} {path}: HTTP {exc.code}: {exc.read().decode()}"
            ) from exc
        if not output.get("success"):
            raise RuntimeError(output)
        return output["result"]


def restorable(record):
    return {
        key: record[key]
        for key in (
            "type",
            "name",
            "content",
            "ttl",
            "proxied",
            "priority",
            "data",
            "comment",
        )
        if key in record and record[key] is not None
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--zone", default="re8ch.com")
    parser.add_argument("--snapshot-dir", default="/tmp")
    args = parser.parse_args()
    token = os.environ.get("CF_API_TOKEN", "")
    if not token:
        raise SystemExit("CF_API_TOKEN is required")

    cloudflare = Cloudflare(token)
    zones = cloudflare.call("GET", "/zones?name=" + urllib.parse.quote(args.zone))
    if len(zones) != 1:
        raise RuntimeError(f"expected exactly one {args.zone} zone, found {len(zones)}")
    zone_id = zones[0]["id"]

    def records(name):
        encoded = urllib.parse.quote(name)
        return cloudflare.call(
            "GET", f"/zones/{zone_id}/dns_records?name={encoded}&per_page=100"
        )

    affected_names = (*APEXES, *AUTHORITIES)
    snapshot = {name: records(name) for name in affected_names}
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_path = pathlib.Path(args.snapshot_dir) / (
        f"cloudflare-leaf-delegation-snapshot-{timestamp}.json"
    )
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps({"zone": zone_id, "records": snapshot}, indent=2) + "\n"
    )
    snapshot_path.chmod(0o600)

    created = []
    deleted = []
    try:
        for name, address in AUTHORITIES.items():
            current = records(name)
            if any(
                record["type"] == "A" and record["content"] == address
                for record in current
            ):
                continue
            if current:
                conflicts = [(record["type"], record["content"]) for record in current]
                raise RuntimeError(f"conflicting glue record at {name}: {conflicts}")
            record = cloudflare.call(
                "POST",
                f"/zones/{zone_id}/dns_records",
                {
                    "type": "A",
                    "name": name,
                    "content": address,
                    "ttl": 300,
                    "proxied": False,
                    "comment": "re8ch regional authority glue",
                },
            )
            created.append(record["id"])

        for apex in APEXES:
            for record in records(apex):
                cloudflare.call(
                    "DELETE", f"/zones/{zone_id}/dns_records/{record['id']}"
                )
                deleted.append(record)
            for authority in AUTHORITIES:
                record = cloudflare.call(
                    "POST",
                    f"/zones/{zone_id}/dns_records",
                    {
                        "type": "NS",
                        "name": apex,
                        "content": authority,
                        "ttl": 300,
                        "comment": "delegated to in-cluster regional authority",
                    },
                )
                created.append(record["id"])
    except Exception:
        for record_id in reversed(created):
            try:
                cloudflare.call("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")
            except Exception:
                pass
        for record in deleted:
            try:
                cloudflare.call(
                    "POST", f"/zones/{zone_id}/dns_records", restorable(record)
                )
            except Exception:
                pass
        raise

    result = {
        "snapshot": str(snapshot_path),
        "delegations": {
            apex: [
                {key: record[key] for key in ("type", "content", "ttl")}
                for record in records(apex)
            ]
            for apex in APEXES
        },
        "glue": {
            name: [
                {key: record[key] for key in ("type", "content", "ttl")}
                for record in records(name)
            ]
            for name in AUTHORITIES
        },
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
