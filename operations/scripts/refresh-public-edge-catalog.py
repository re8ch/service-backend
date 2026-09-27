#!/usr/bin/env python3
"""Refresh the read-only Headlamp inventory from configured cloud CLI accounts.

Only public hostname and routing metadata is exported. DNS record values,
particularly TXT verification values, are deliberately excluded.
"""

import json
import subprocess
import sys
from pathlib import Path
from textwrap import indent

import yaml


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "platform/headlamp/public-edge-catalog.yaml"


def query(*args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"Query failed: {args[:3]}")
    return json.loads(result.stdout)


def pages(fetch, items_key, total_key="TotalCount"):
    found = []
    page = 0
    while True:
        response = fetch(page)
        found.extend(response.get(items_key, []))
        total = response.get(total_key, len(found))
        if len(found) >= total:
            return found
        if not response.get(items_key):
            raise RuntimeError(f"Incomplete {items_key} listing: {len(found)}/{total}")
        page += 1


def main():
    manifest = yaml.safe_load(CATALOG.read_text())
    current = json.loads(manifest["data"]["sites.json"])
    manual = [site for site in current.get("sites", []) if site.get("sourceOfTruth") == "manual"]
    notes = {host: site.get("notes") for site in current.get("sites", [])
             for host in site.get("hostnames", []) if site.get("notes")}
    sites = []

    zones = pages(lambda page: query("tccli", "teo", "DescribeZones", "--Offset", str(page * 200), "--Limit", "200"), "Zones")
    for zone in zones:
        zone_id = zone["ZoneId"]
        try:
            domains = pages(lambda page: query("tccli", "teo", "DescribeAccelerationDomains", "--ZoneId", zone_id,
                                                "--Offset", str(page * 200), "--Limit", "200"), "AccelerationDomains")
        except RuntimeError as exc:
            if "UnauthorizedOperation" not in str(exc):
                raise
            print(f"Skipping inaccessible Tencent zone {zone['ZoneName']} ({zone_id})", file=sys.stderr)
            sites.extend(site for site in current.get("sites", [])
                         if site.get("sourceOfTruth") == f"tccli teo/{zone['ZoneName']}")
            continue
        for domain in domains:
            host = domain["DomainName"].rstrip(".")
            origin = domain.get("OriginDetail") or {}
            sites.append({
                "name": host,
                "hostnames": [host],
                "provider": "Tencent EdgeOne",
                "delivery": domain.get("DomainStatus", "unknown"),
                "origin": origin.get("Origin") or "-",
                "dnsMode": "EdgeOne acceleration domain",
                "sourceOfTruth": f"tccli teo/{zone['ZoneName']}",
                **({"notes": notes[host]} if host in notes else {}),
            })

    ali_sites = pages(lambda page: query("aliyun", "esa", "ListSites", "--region", "cn-hangzhou",
                                         "--PageNumber", str(page + 1), "--PageSize", "500"), "Sites")
    for site in ali_sites:
        site_id = str(site["SiteId"])
        records = pages(lambda page: query("aliyun", "esa", "ListRecords", "--region", "cn-hangzhou",
                                           "--SiteId", site_id, "--PageNumber", str(page + 1), "--PageSize", "500"), "Records")
        for record in records:
            host = record["RecordName"].rstrip(".")
            record_type = record.get("RecordType", "unknown")
            proxied = record.get("Proxied") is True
            sites.append({
                "name": host,
                "hostnames": [host],
                "provider": "Aliyun ESA",
                "delivery": "CDN proxied" if proxied else "DNS only",
                "origin": "-",  # Never export record Data or TXT verification values.
                "dnsMode": record_type,
                "sourceOfTruth": f"aliyun esa/{site['SiteName']}",
            })

    sites.extend(manual)
    sites.sort(key=lambda site: (site["provider"], site["hostnames"][0], site["dnsMode"]))
    catalog_json = json.dumps({"sites": sites}, ensure_ascii=False, indent=2) + "\n"
    manifest.pop("data")
    CATALOG.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)
                       + "data:\n  sites.json: |\n" + indent(catalog_json, "    "))
    print(f"Wrote {len(sites)} provider records to {CATALOG}")


if __name__ == "__main__":
    main()
