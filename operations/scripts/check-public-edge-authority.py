#!/usr/bin/env python3
"""Check PublicEdge delegation, public UDP/TCP 53 and selected DNS answers."""

import hashlib
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone


def dig(server, name, record, tcp=False):
    command = ["dig", f"@{server}", name, record, "+time=2", "+tries=1",
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


def main():
    config_bytes = pathlib.Path(sys.argv[1]).read_bytes()
    config = json.loads(config_bytes)
    failures = []
    observations = []
    expected_ns = {name.rstrip(".").lower() + "." for name in config["nameservers"]}
    admitted = set(config["admittedPublicIps"])
    for zone in config["zones"]:
        parent = dig("1.1.1.1", zone, "NS")
        found_ns = {row[-1].lower() for row in parent["answers"] if len(row) >= 5 and row[-2] == "NS"}
        if not parent["ok"] or found_ns != expected_ns:
            failures.append(f"{zone}: parent NS mismatch: {sorted(found_ns)}")
        for ns in expected_ns:
            glue = dig("1.1.1.1", ns, "A")
            ips = {row[-1] for row in glue["answers"] if len(row) >= 5 and row[-2] == "A"}
            if not glue["ok"] or not ips or not ips.issubset(admitted):
                failures.append(f"{ns}: public A not admitted: {sorted(ips)}")
        for ip in admitted:
            for tcp in (False, True):
                for record in ("SOA", "A"):
                    response = dig(ip, zone, record, tcp)
                    ok = response["ok"] and response["authoritative"] and bool(response["answers"])
                    observations.append({"ip": ip, "zone": zone, "transport": "tcp" if tcp else "udp",
                                         "record": record, "ok": ok,
                                         "answers": [" ".join(row) for row in response["answers"]]})
                    if not ok:
                        failures.append(f"{ip}: {zone} {record} {'tcp' if tcp else 'udp'} unavailable")
    print(json.dumps({"observedAt": datetime.now(timezone.utc).isoformat(),
                      "configSha256": hashlib.sha256(config_bytes).hexdigest(),
                      "observations": observations, "failures": failures}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
