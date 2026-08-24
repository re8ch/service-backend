#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mode="${1:-}"
case "$mode" in reject-all|allow-one) ;; *) printf 'usage: %s [reject-all|allow-one]\n' "$0" >&2; exit 1 ;; esac

kubectl -n akash-services get statefulset akash-provider >/dev/null
work_dir="$(mktemp -d)"
trap 'rm -rf -- "$work_dir"' EXIT
script="$script_dir/pricing/reject-all.sh"

if [[ "$mode" == allow-one ]]; then
  : "${AKASH_TEST_TENANT:?AKASH_TEST_TENANT is required}"
  [[ "$AKASH_TEST_TENANT" == akash1* ]] || { printf 'invalid Akash tenant address\n' >&2; exit 1; }
  active="$(kubectl -n lease get manifests.akash.network -o json 2>/dev/null | jq '.items | length' || printf '0')"
  [[ "$active" == "0" ]] || { printf 'refusing allow-one: %s manifest(s) already exist\n' "$active" >&2; exit 1; }
  sed "s#__AKASH_TEST_TENANT__#${AKASH_TEST_TENANT}#g" "$script_dir/pricing/allow-one-tenant.sh" > "$work_dir/price.sh"
  script="$work_dir/price.sh"
fi

helm get values akash-provider -n akash-services -o yaml > "$work_dir/current-values.yaml"
printf 'bidpricescript: %q\n' "$(openssl base64 -A -in "$script")" > "$work_dir/bid-values.yaml"
helm upgrade akash-provider akash/provider --version 19.0.1 -n akash-services \
  --reuse-values -f "$work_dir/bid-values.yaml" --wait --timeout 5m
printf 'Bid mode is now %s.\n' "$mode"
