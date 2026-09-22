#!/usr/bin/env bash
set -euo pipefail

: "${KUBECTL:=sudo k3s kubectl}"

ready="$(${KUBECTL} --request-timeout=15s get --raw=/readyz?verbose)"
printf '%s\n' "$ready"
grep -q '^\[+\]etcd ok' <<<"$ready"
grep -q '^\[+\]etcd-readiness ok' <<<"$ready"

not_ready="$(${KUBECTL} get nodes --no-headers | awk '$2 !~ /^Ready(,|$)/ {print $1 ":" $2}')"
if [[ -n "$not_ready" ]]; then
  echo "Nodes not Ready: $not_ready" >&2
  exit 1
fi

${KUBECTL} get node r640 overseas-edge-50 >/dev/null
${KUBECTL} get storageclass local-path >/dev/null
${KUBECTL} -n llm-gateway get secret re8ch-wildcard-tls >/dev/null
${KUBECTL} get ingressclass traefik >/dev/null

curl -fsS --max-time 10 https://headlamp.service.re8ch.com/ >/dev/null
qwen_code="$(curl -ksS -o /dev/null -w '%{http_code}' --max-time 10 https://api-guangzhou24.re8ch.com/ || true)"
case "$qwen_code" in
  200|401|403|404) ;;
  *) echo "Qwen regional smoke failed: HTTP ${qwen_code}" >&2; exit 1 ;;
esac

echo "NetBird preflight passed."
