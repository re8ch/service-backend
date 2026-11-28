#!/usr/bin/env python3
"""Check elected PublicEdge delegation and public UDP/TCP 53 authority."""

import hashlib
import ipaddress
import json
import os
import pathlib
import subprocess
import sys
import time
from datetime import datetime, timezone


UDP_RETRY_DELAY_SECONDS = 1.0


def dig(server, name, record, tcp=False, retry_empty=True):
    command = ["dig", f"@{server}", name, record, "+time=2", "+tries=2",
               "+noall", "+comments", "+answer"]
    if tcp:
        command.append("+tcp")
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=8)
    except subprocess.TimeoutExpired:
        result = subprocess.CompletedProcess(command, 9, "", "dig: subprocess timeout")
    output = result.stdout + result.stderr
    # dig exits 9 ("no reply from server") on timeouts. A UDP-only timeout from
    # one vantage is a path symptom, not evidence of divergent authority data,
    # so retry once before recording it.
    if result.returncode == 9 and not tcp and retry_empty:
        time.sleep(UDP_RETRY_DELAY_SECONDS)
        return dig(server, name, record, tcp, retry_empty=False)
    header = next((line for line in output.splitlines() if "opcode: QUERY" in line), "")
    flags = next((line for line in output.splitlines() if line.startswith(";; flags:")), "")
    answers = [line.split() for line in output.splitlines() if line and not line.startswith(";")]
    return {"ok": result.returncode == 0 and "status: NOERROR" in header,
            "timeout": result.returncode == 9,
            "authoritative": " aa " in f" {flags} ",
            "answers": answers, "error": output.strip()[-300:] if result.returncode else ""}


def values(response, record):
    return {row[-1].lower().rstrip(".") for row in response["answers"]
            if len(row) >= 5 and row[-2] == record}


def _key(observation):
    return observation["ip"], observation["zone"], observation["record"]


def authority_failures(observations):
    """Failures that indicate wrong or missing authority data.

    A UDP timeout counts as a failure only when TCP for the same server, zone
    and record also failed. When TCP returned the expected authoritative answer
    the server data is correct and the UDP loss is reported as degradation.
    """
    tcp_ok = {_key(o) for o in observations if o["transport"] == "tcp" and o["ok"]}
    failures = []
    for o in observations:
        if o["ok"]:
            continue
        transport = o["transport"]
        if transport == "udp" and o.get("timeout") and _key(o) in tcp_ok:
            continue
        kind = "timeout" if o.get("timeout") else ("missing" if o["record"] == "A" else "mismatch")
        failures.append(f"{o['ip']}: {o['zone']} {o['record']} {transport} {kind}")
    return failures


def udp_path_degradations(observations):
    tcp_ok = {_key(o) for o in observations if o["transport"] == "tcp" and o["ok"]}
    return [f"{o['ip']}: {o['zone']} {o['record']} udp timeout (tcp authoritative ok)"
            for o in observations
            if o["transport"] == "udp" and not o["ok"] and o.get("timeout") and _key(o) in tcp_ok]


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
                                         "record": record, "ok": ok, "timeout": response["timeout"],
                                         "answers": [" ".join(row) for row in response["answers"]]})
    for zone in config.get("addressZones", []):
        for ns, ip in addresses.items():
            for tcp in (False, True):
                response = dig(ip, zone, "A", tcp)
                found = values(response, "A")
                observations.append({"ip": ip, "zone": zone,
                                     "transport": "tcp" if tcp else "udp", "record": "A",
                                     "ok": response["ok"] and response["authoritative"] and bool(found),
                                     "timeout": response["timeout"],
                                     "answers": [" ".join(row) for row in response["answers"]]})
    failures.extend(authority_failures(observations))
    degraded = udp_path_degradations(observations)
    verdict = "fail" if failures else ("degraded" if degraded else "pass")
    print(json.dumps({"observedAt": datetime.now(timezone.utc).isoformat(),
                      "vantage": os.getenv("PUBLIC_EDGE_VANTAGE", "unspecified"),
                      "configSha256": hashlib.sha256(config_bytes).hexdigest(),
                      "verdict": verdict,
                      "nameservers": addresses, "observations": observations,
                      "failures": failures, "degraded": degraded}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
