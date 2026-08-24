#!/usr/bin/env bash
set -euo pipefail
: "${KUBECTL:=sudo k3s kubectl}"

# Copy the existing embedded relay authentication secret without decoding it to
# the terminal, command arguments, repository or shell history.
${KUBECTL} -n netbird-access get secret netbird-server-config \
  -o jsonpath='{.data.config\.yaml}' \
  | base64 -d \
  | python3 -c '
import base64, json, re, sys
config = sys.stdin.read()
match = re.search(r"(?m)^\s*authSecret:\s*[^A-Za-z0-9+/=_-]?([A-Za-z0-9+/=_-]+)", config)
if not match:
    raise SystemExit("embedded relay authSecret not found")
print(json.dumps({"apiVersion":"v1","kind":"Secret","type":"Opaque",
 "metadata":{"name":"netbird-external-relay-auth","namespace":"netbird-access"},
 "data":{"NB_AUTH_SECRET":base64.b64encode(match.group(1).encode()).decode()}}))
' | ${KUBECTL} apply -f -
