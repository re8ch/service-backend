#!/bin/sh
set -eu

ca_key=/var/lib/re8ch-ssh-ca/ssh_user_ca
public_key=${1:?public key path required}
operation_id=${2:?operation id required}
principal=${3:-re8ch-ai-operate}

case $principal in
  re8ch-ai-observe|re8ch-ai-operate) ;;
  *) echo "unapproved principal" >&2; exit 2 ;;
esac
case $operation_id in
  *[!A-Za-z0-9._:-]*|'') echo "invalid operation id" >&2; exit 2 ;;
esac
[ -f "$ca_key" ] || { echo "CA is not initialized" >&2; exit 1; }
ssh-keygen -q -s "$ca_key" -I "$operation_id" -n "$principal" -V -1m:+10m "$public_key"
logger -t re8ch-ssh-ca "issued operation=$operation_id principal=$principal ttl=10m"
