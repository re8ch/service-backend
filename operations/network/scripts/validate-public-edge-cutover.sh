#!/usr/bin/env bash
set -euo pipefail
if (( $# < 2 )); then echo "usage: $0 <server-ip> <server-ip> [...]" >&2; exit 2; fi
zones=(auth.re8ch.com api.re8ch.com db-ro.re8ch.com db-rw.re8ch.com service.re8ch.com functions.re8ch.com test.re8ch.com)
servers=("$@")
failed=0
for zone in "${zones[@]}"; do
  baseline_serial=""; baseline_ns=""
  for server in "${servers[@]}"; do
    response=$(dig +tcp +time=3 +tries=1 +noall +answer +comments "@$server" "$zone" SOA)
    if grep -q 'status: NXDOMAIN' <<<"$response"; then echo "FAIL zone=$zone server=$server reason=NXDOMAIN"; failed=1; continue; fi
    serial=$(awk '$4 == "SOA" {print $7; exit}' <<<"$response")
    if [[ -z "$serial" ]]; then echo "FAIL zone=$zone server=$server reason=missing-SOA"; failed=1; continue; fi
    ns=$(dig +tcp +time=3 +tries=1 +short "@$server" "$zone" NS | sort | tr '\n' ' ')
    baseline_serial=${baseline_serial:-$serial}; baseline_ns=${baseline_ns:-$ns}
    if [[ "$serial" != "$baseline_serial" || "$ns" != "$baseline_ns" ]]; then
      echo "FAIL zone=$zone server=$server serial=$serial expected=$baseline_serial ns=[$ns] expected-ns=[$baseline_ns]"; failed=1
    fi
  done
done
declare -A subnet=([CN]=223.5.5.0/24 [US]=8.8.8.0/24 [EU]=80.80.80.0/24)
declare -A expected=([CN]=223.159.210.222 [US]=179.255.126.104 [EU]=2.28.106.230)
for region in CN US EU; do
  baseline=""
  for server in "${servers[@]}"; do
    answer=$(dig +tcp +time=3 +tries=1 +short +subnet="${subnet[$region]}" "@$server" headlamp.service.re8ch.com A | sort | tr '\n' ' ')
    baseline=${baseline:-$answer}
    if [[ -z "$answer" || "$answer" != "$baseline" || "$answer" != *"${expected[$region]}"* ]]; then
      echo "FAIL region=$region server=$server answer=[$answer] expected=${expected[$region]} baseline=[$baseline]"; failed=1
    fi
  done
done
if (( failed )); then echo "delegation gate failed; do not change apex NS or parent delegation" >&2; exit 1; fi
echo "all zones, authorities, and regional answers are consistent"
