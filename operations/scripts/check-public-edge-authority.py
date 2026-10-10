#!/usr/bin/env python3
"""Check elected PublicEdge delegation and public UDP/TCP 53 authority."""

import hashlib
import ipaddress
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone


def dig(server, name, record, tcp=False):
    command = ["dig", f"@{server}", name, record, "+time=2", "+tries=2",
               "+noall", "+comments", "+answer"]
    if tcp:
        command.append("+tcp")
    result = subprocess.run(command, capture_output=True, text=True, timeout=8)
    output = result.stdout + result.stderr
    header = next((line for line in output.splitlines() if "opcode: QUERY" in line), "")
    flags = next((line for line in output.splitlines() if line.startswith(";; flags:")), "")
    answers = [line.split() for line in output.splitlines() if line and not line.startswith(";")]
    return {"ok": result.returncode == 0 and "status: NOERROR" in header,
            "authoritative": " aa " in f" {flags} ",
            "answers": answers, "error": output.strip()[-300:] if result.returncode else ""}


def values(response, record):
    return {row[-1].lower().rstrip(".") for row in response["answers"]
            if len(row) >= 5 and row[-2] == record}


def main():
    config_bytes = pathlib.Path(sys.argv[1]).read_bytes()
    config = json.loads(config_bytes)
    failures = []
    observations = []
    max_nameservers = config.get("maxNameservers", 3)
    min_nameservers = config.get("minPrimaryNameservers", 1)
    secondary = {zone: {name.lower().rstrip(".") for name in names}
                 for zone, names in config.get("secondaryNameserversByZone", {}).items()}
    zone_sets = {}
    primary_sets = {}
    for zone in config["zones"]:
        parent = dig("1.1.1.1", zone, "NS")
        if not parent["ok"] or not values(parent, "NS"):
            parent = dig("8.8.8.8", zone, "NS")
        selected = values(parent, "NS")
        zone_sets[zone] = selected
        expected_secondary = secondary.get(zone, set())
        primary_sets[zone] = selected - expected_secondary
        if not parent["ok"] or not min_nameservers <= len(primary_sets[zone]) <= max_nameservers:
            failures.append(f"{zone}: expected {min_nameservers}..{max_nameservers} primary NS: {sorted(primary_sets[zone])}")
        if not expected_secondary.issubset(selected):
            failures.append(f"{zone}: missing configured secondary NS: {sorted(expected_secondary - selected)}")
    unique_sets = {frozenset(selected) for selected in primary_sets.values()}
    if len(unique_sets) != 1:
        failures.append("delegated zones disagree on primary NS set")
    nameservers = sorted(next(iter(unique_sets), frozenset()))
    addresses = {}
    for ns in nameservers:
        glue = dig("1.1.1.1", ns, "A")
        ips = values(glue, "A")
        try:
            public = {ip for ip in ips if ipaddress.ip_address(ip).is_global}
        except ValueError:
            public = set()
        if not glue["ok"] or len(public) != 1 or public != ips:
            failures.append(f"{ns}: expected exactly one public A: {sorted(ips)}")
        else:
            addresses[ns] = next(iter(public))
    if len(set(addresses.values())) != len(addresses):
        failures.append("multiple NS names point to the same public server")
    for zone, selected in zone_sets.items():
        for ns, ip in addresses.items():
            for tcp in (False, True):
                for record in ("SOA", "NS"):
                    response = dig(ip, zone, record, tcp)
                    found = values(response, "NS") if record == "NS" else set()
                    ok = response["ok"] and response["authoritative"] and bool(response["answers"])
                    if record == "NS":
                        ok = ok and found == selected
                    observations.append({"ip": ip, "zone": zone, "transport": "tcp" if tcp else "udp",
                                         "record": record, "ok": ok,
                                         "answers": [" ".join(row) for row in response["answers"]]})
                    if not ok:
                        failures.append(f"{ip}: {zone} {record} {'tcp' if tcp else 'udp'} mismatch")
    for zone in config.get("addressZones", []):
        for ns, ip in addresses.items():
            for tcp in (False, True):
                response = dig(ip, zone, "A", tcp)
                found = values(response, "A")
                if not response["ok"] or not response["authoritative"] or not found:
                    failures.append(f"{ip}: {zone} A {'tcp' if tcp else 'udp'} missing")
                observations.append({"ip": ip, "zone": zone,
                                     "transport": "tcp" if tcp else "udp", "record": "A",
                                     "ok": response["ok"] and response["authoritative"] and bool(found),
                                     "answers": [" ".join(row) for row in response["answers"]]})
    print(json.dumps({"observedAt": datetime.now(timezone.utc).isoformat(),
                      "configSha256": hashlib.sha256(config_bytes).hexdigest(),
                      "nameservers": addresses, "observations": observations,
                      "failures": failures}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
