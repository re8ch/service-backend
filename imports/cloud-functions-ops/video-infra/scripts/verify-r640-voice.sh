#!/usr/bin/env bash
set -euo pipefail

fail=0
check() { "$@" || { echo "FAIL: $*" >&2; fail=1; }; }
check systemctl is-active --quiet freeswitch
check systemctl is-active --quiet re8ch-voice-gateway
check ss -H -lun 'sport = :5160'
check ss -H -lun 'sport = :5080'
check ss -H -ltn 'sport = :8021'
check curl --fail --silent --show-error http://192.0.2.169:18092/healthz
/opt/freeswitch/bin/fs_cli -H 127.0.0.1 -P 8021 -p "$(sed -n 's/^FREESWITCH_ESL_PASSWORD=//p' /etc/re8ch-voice/voice-gateway.env)" -x 'sofia status'
if ss -H -lunp | grep -q '192.0.2.169:5160.*gxw_sip_autoanswer'; then echo "FAIL: old autoanswer owns 5160" >&2; fail=1; fi
exit "$fail"
