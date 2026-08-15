#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mode="${1:-render}"
provider_chart_version="19.0.1"
gateway_chart_version="1.2.1"
ngf_version="2.6.7"
provider_version="v0.16.1"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "$1 is required"; }
need helm
need kubectl
need openssl
need jq

case "$mode" in render|apply-infra|apply-provider) ;; *) die "usage: $0 [render|apply-infra|apply-provider]" ;; esac

work_dir="$(mktemp -d)"
cleanup() { rm -rf -- "$work_dir"; }
trap cleanup EXIT

helm repo add akash https://akash-network.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update >/dev/null

render_admission() {
  local image="${AKASH_ADMISSION_IMAGE:-registry.re8ch.com/ops/akash-lease-admission:REQUIRED}"
  local ca_bundle="${AKASH_ADMISSION_CA_BUNDLE:-REQUIRED}"
  sed -e "s#__AKASH_ADMISSION_IMAGE__#${image}#g" \
      -e "s#__AKASH_ADMISSION_CA_BUNDLE__#${ca_bundle}#g" \
      "$script_dir/admission.yaml"
}

render_all() {
  kubectl kustomize "https://github.com/nginx/nginx-gateway-fabric/config/crd/gateway-api/experimental?ref=v${ngf_version}"
  curl -fsSL "https://raw.githubusercontent.com/akash-network/provider/${provider_version}/pkg/apis/akash.network/crd.yaml"
  helm template ngf oci://ghcr.io/nginx/charts/nginx-gateway-fabric --version "$ngf_version" -n nginx-gateway -f "$script_dir/values-ngf.yaml"
  helm template akash-gateway akash/akash-gateway --version "$gateway_chart_version" -n akash-gateway -f "$script_dir/values-gateway.yaml"
  helm template akash-hostname-operator akash/akash-hostname-operator --version "$provider_chart_version" -n akash-services -f "$script_dir/values-hostname.yaml"
  helm template inventory-operator akash/akash-inventory-operator --version "$provider_chart_version" -n akash-services -f "$script_dir/values-inventory.yaml"
  render_admission
}

if [[ "$mode" == render ]]; then
  render_all > "$work_dir/rendered.yaml"
  if command -v yq >/dev/null 2>&1; then
    yq eval-all 'true' "$script_dir"/*.yaml >/dev/null
  fi
  printf 'Rendered %s objects; output validated locally.\n' "$(grep -c '^kind:' "$work_dir/rendered.yaml")"
  exit 0
fi

[[ "${ALLOW_UNSUPPORTED_K3S:-}" == "true" ]] || die "set ALLOW_UNSUPPORTED_K3S=true to acknowledge the guarded K3s experiment"
ALLOW_UNSUPPORTED_K3S=true "$script_dir/preflight.sh"

if [[ "$mode" == apply-infra ]]; then
  [[ -n "${AKASH_ADMISSION_IMAGE:-}" ]] || die "AKASH_ADMISSION_IMAGE is required"
  [[ "$AKASH_ADMISSION_IMAGE" != *:latest ]] || die "admission image must not use latest"

  kubectl apply -f "$script_dir/namespaces-and-policy.yaml"

  openssl req -x509 -newkey rsa:3072 -nodes -days 365 \
    -subj '/CN=akash-lease-admission.akash-services.svc' \
    -addext 'subjectAltName=DNS:akash-lease-admission.akash-services.svc,DNS:akash-lease-admission.akash-services.svc.cluster.local' \
    -keyout "$work_dir/tls.key" -out "$work_dir/tls.crt" >/dev/null 2>&1
  kubectl -n akash-services create secret tls akash-lease-admission-tls \
    --cert="$work_dir/tls.crt" --key="$work_dir/tls.key" --dry-run=client -o yaml | kubectl apply -f -
  export AKASH_ADMISSION_CA_BUNDLE
  AKASH_ADMISSION_CA_BUNDLE="$(openssl base64 -A -in "$work_dir/tls.crt")"
  render_admission | kubectl apply -f -
  kubectl -n akash-services rollout status deployment/akash-lease-admission --timeout=180s

  kubectl kustomize "https://github.com/nginx/nginx-gateway-fabric/config/crd/gateway-api/experimental?ref=v${ngf_version}" | kubectl apply --server-side -f -
  curl -fsSL "https://raw.githubusercontent.com/akash-network/provider/${provider_version}/pkg/apis/akash.network/crd.yaml" | kubectl apply --server-side -f -

  helm upgrade --install ngf oci://ghcr.io/nginx/charts/nginx-gateway-fabric --version "$ngf_version" \
    -n nginx-gateway --create-namespace -f "$script_dir/values-ngf.yaml" --wait --timeout 10m

  for secret_name in wildcard-ingress-tls akash-default-tls; do
    kubectl -n akash-gateway get secret "$secret_name" >/dev/null 2>&1 || die "TLS Secret akash-gateway/$secret_name is required before Gateway install"
  done
  helm upgrade --install akash-gateway akash/akash-gateway --version "$gateway_chart_version" \
    -n akash-gateway -f "$script_dir/values-gateway.yaml" --wait --timeout 10m
  helm upgrade --install akash-hostname-operator akash/akash-hostname-operator --version "$provider_chart_version" \
    -n akash-services -f "$script_dir/values-hostname.yaml" --wait --timeout 10m
  helm upgrade --install inventory-operator akash/akash-inventory-operator --version "$provider_chart_version" \
    -n akash-services -f "$script_dir/values-inventory.yaml" --wait --timeout 10m
  ngf_service="$(kubectl -n akash-gateway get service -l gateway.networking.k8s.io/gateway-name=akash-gateway -o json | jq -r '.items | if length == 1 then .[0].metadata.name else empty end')"
  [[ -n "$ngf_service" ]] || die "could not resolve the single NGF Service owned by akash-gateway"
  sed "s#__NGF_SERVICE_NAME__#${ngf_service}#g" "$script_dir/edge-routes.template.yaml" | kubectl apply -f -
  printf 'Infrastructure installed. Provider remains absent and no chain transaction was sent.\n'
  exit 0
fi

[[ "${REGISTER_SANDBOX_PROVIDER:-}" == "yes" ]] || die "set REGISTER_SANDBOX_PROVIDER=yes to authorize the Sandbox provider transaction"
: "${AKASH_PROVIDER_ADDRESS:?AKASH_PROVIDER_ADDRESS is required}"
: "${AKASH_PROVIDER_KEY_FILE:?AKASH_PROVIDER_KEY_FILE is required}"
: "${AKASH_PROVIDER_KEY_PASSWORD_FILE:?AKASH_PROVIDER_KEY_PASSWORD_FILE is required}"
: "${AKASH_PROVIDER_EMAIL:?AKASH_PROVIDER_EMAIL is required}"
[[ -s "$AKASH_PROVIDER_KEY_FILE" ]] || die "provider key file is empty"
[[ -s "$AKASH_PROVIDER_KEY_PASSWORD_FILE" ]] || die "provider key password file is empty"

for required in \
  deployment/akash-lease-admission \
  deployment/akash-hostname-operator \
  deployment/inventory-operator; do
  kind="${required%%/*}"; name="${required#*/}"
  kubectl -n akash-services get "$kind" "$name" >/dev/null || die "missing akash-services/$required"
done
kubectl -n akash-gateway wait --for=condition=Programmed gateway/akash-gateway --timeout=180s

provider_values="$work_dir/provider-secret-values.yaml"
umask 077
{
  printf 'from: %q\n' "$AKASH_PROVIDER_ADDRESS"
  printf 'email: %q\n' "$AKASH_PROVIDER_EMAIL"
  printf 'key: %q\n' "$(openssl base64 -A -in "$AKASH_PROVIDER_KEY_FILE")"
  printf 'keysecret: %q\n' "$(openssl base64 -A -in "$AKASH_PROVIDER_KEY_PASSWORD_FILE")"
  printf 'bidpricescript: %q\n' "$(openssl base64 -A -in "$script_dir/pricing/reject-all.sh")"
} > "$provider_values"

helm upgrade --install akash-provider akash/provider --version "$provider_chart_version" \
  -n akash-services -f "$script_dir/values-provider.yaml" -f "$provider_values" --wait --timeout 10m
printf 'Sandbox Provider installed with reject-all bidding. Do not enable test bidding until verify.sh passes.\n'
