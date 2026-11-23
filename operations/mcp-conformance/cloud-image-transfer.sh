#!/usr/bin/env bash
# Native OCI transfer and independent byte proof; never compiles an image.
set -euo pipefail
mode=${1:?prove or mirror}
source=${2:?immutable source}
target=${3:?target repository}
prefix=${4:?evidence prefix}
digest=${source##*@}
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 2
source_repo=${source%@*}
local_test=false
if [[ "$source_repo" == "127.0.0.1:5000/re8ch-cloud-platform" && "$target" == "127.0.0.1:5001/re8ch-cloud-platform" ]]; then
  local_test=true
elif [[ "$source_repo" != 'ghcr.io/re8ch/service-backend-cloud-platform' || "$target" != 'registry.re8ch.com/functions-shared/re8ch-cloud-platform' ]]; then
  echo 'Unreviewed transfer repositories' >&2
  exit 2
fi
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
work=$(mktemp -d "${RUNNER_TEMP:-/tmp}/cloud-oci-transfer.XXXXXX")
trap 'rm -rf "$work"' EXIT
issuer=https://token.actions.githubusercontent.com
identity=https://github.com/re8ch/service-backend/.github/workflows/cloud-platform-release.yml@refs/heads/main
verify_args=(--certificate-identity "$identity" --certificate-oidc-issuer "$issuer")
sign_args=(--yes)
cp_args=()
discover_args=()
if $local_test; then
  : "${CLOUD_CI_SIGNING_KEY:?ephemeral test key required}"
  verify_args=(--key "$CLOUD_CI_SIGNING_KEY.pub" --insecure-ignore-tlog=true --allow-insecure-registry)
  sign_args=(--yes --key "$CLOUD_CI_SIGNING_KEY.key" --tlog-upload=false --allow-insecure-registry)
  cp_args=(--from-plain-http --to-plain-http)
  discover_args=(--plain-http)
fi
prove() {
  local image=$1 output=$2 name=$3
  # Each proof has a new content store. Docker's existing layer cache cannot
  # turn this into a metadata-only pull. No raw layer is exported as evidence.
  local args=()
  if $local_test; then args=(--from-plain-http); fi
  if [[ "$name" == source ]]; then
    # Client-only failure injection, never changes/stops production Harbor.
    local denied=registry.re8ch.com:443:127.0.0.1:1
    if $local_test; then denied=127.0.0.1:5001:127.0.0.1:1; fi
    if oras manifest fetch "${discover_args[@]}" --resolve "$denied" "$target@$digest" >/dev/null 2> "$work/denied-error"; then
      echo 'Isolated Harbor failure injection did not fail' >&2; exit 1
    fi
    # A new replica may legitimately return 404; that is not an outage proof.
    grep -Fq '127.0.0.1:1: connect: connection refused' "$work/denied-error" || {
      echo 'Harbor failure injection was not the intended loopback connection refusal' >&2; exit 1;
    }
    args+=(--from-resolve "$denied")
  fi
  oras cp --recursive "${args[@]}" "$image" --to-oci-layout "$work/$name:proof" >/dev/null
  python3 "$script_dir/verify-image-evidence.py" layout "$work/$name" "$digest" > "$output"
  if [[ "$name" == source ]]; then
    jq '. + {harborDeniedForClient:true,harborFailureClass:"connection_refused"}' "$output" > "$work/source-proof.json"
    cp "$work/source-proof.json" "$output"
  fi
}
if [[ "$mode" == prove ]]; then
  cosign verify "${verify_args[@]}" "$source" > "$prefix.primary-signature.json"
  python3 "$script_dir/verify-image-evidence.py" claim "$prefix.primary-signature.json" "$digest" "$source_repo"
  prove "$source" "$prefix.primary-cold.json" source
  exit 0
fi
[[ "$mode" == mirror ]] || exit 2
cosign verify "${verify_args[@]}" "$source" > "$prefix.source-signature.json"
# OCI referrers and all child manifests are copied without rewriting bytes.
oras cp --recursive "${cp_args[@]}" "$source" "$target:${CERTIFIED_SOURCE_SHA:?}" >/dev/null
oras discover "${discover_args[@]}" --format json "$source" > "$prefix.source-referrers.json"
oras discover "${discover_args[@]}" --format json "$target@$digest" > "$prefix.target-referrers.json"
python3 - "$prefix" <<'PY'
import json, re, sys
from pathlib import Path
prefix = sys.argv[1]
def digests(value):
    if isinstance(value, dict):
        found = {value['digest']} if re.fullmatch(r'sha256:[0-9a-f]{64}', str(value.get('digest', ''))) else set()
        for child in value.values(): found |= digests(child)
        return found
    if isinstance(value, list):
        return set().union(*(digests(child) for child in value))
    return set()
source = digests(json.loads(Path(prefix + '.source-referrers.json').read_text()))
target = digests(json.loads(Path(prefix + '.target-referrers.json').read_text()))
if not source <= target:
    sys.exit('Target omitted source OCI referrers')
PY
# Cosign v2 legacy attachments are tags rather than OCI referrers. Copy their
# native triangulated tags, never cosign-copy's synthetic sha256-DIGEST image
# tag: it collides with the OCI referrers fallback index used by ORAS.
existing=false
if cosign verify "${verify_args[@]}" "$target@$digest" > "$work/existing.json" 2>/dev/null; then
  if python3 - "$work/existing.json" "$source_repo" "$target" "$digest" <<'PY'
import json, sys
p, source, target, digest = sys.argv[1:]
repos = {x.get('critical', {}).get('identity', {}).get('docker-reference') for x in json.load(open(p)) if x.get('critical', {}).get('image', {}).get('docker-manifest-digest') == digest}
sys.exit(0 if {source, target} <= repos else 1)
PY
  then existing=true; fi
fi
if ! $existing; then
  for kind in signature attestation sbom; do
    attachment=$(cosign triangulate --type "$kind" "$source")
    if oras manifest fetch "${discover_args[@]}" --descriptor "$attachment" > "$work/$kind.json" 2> "$work/$kind-error"; then
      oras cp --recursive "${cp_args[@]}" "$attachment" "$target:${attachment##*:}" >/dev/null
    elif [[ "$kind" != signature ]] && grep -Eq '(404 Not Found|: not found|MANIFEST_UNKNOWN|NAME_UNKNOWN)' "$work/$kind-error"; then
      continue
    else
      echo 'Cannot inspect or copy a required legacy signature attachment' >&2
      exit 1
    fi
  done
  cosign sign "${sign_args[@]}" "$target@$digest"
fi
cosign verify "${verify_args[@]}" "$target@$digest" > "$prefix.target-signature.json"
python3 - "$prefix.target-signature.json" "$source_repo" "$target" "$digest" <<'PY'
import json, sys
p, source, target, digest = sys.argv[1:]
repos = {x.get('critical', {}).get('identity', {}).get('docker-reference') for x in json.load(open(p)) if x.get('critical', {}).get('image', {}).get('docker-manifest-digest') == digest}
if not {source, target} <= repos:
    sys.exit('Destination lacks preserved source proof or its own repository claim')
PY
prove "$target@$digest" "$prefix.harbor-cold.json" target
printf '%s@%s\n' "$target" "$digest" > "$prefix.harbor.image"
