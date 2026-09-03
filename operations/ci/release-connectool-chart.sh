#!/usr/bin/env sh
set -eu

: "${CONNECTOOL_USERNAME:?set CONNECTOOL_USERNAME as a protected CI variable}"
: "${CONNECTOOL_PASSWORD:?set CONNECTOOL_PASSWORD as a protected masked CI variable}"
: "${SIGSTORE_ID_TOKEN:?GitLab must issue the sigstore ID token}"

chart_dir="clusters/re8ch/charts/connectool"
registry="${CONNECTOOL_HOST:-registry.re8ch.com}"
repository="${CONNECTOOL_REPOSITORY:-helm/connectool}"
version=$(helm show chart "$chart_dir" | awk '$1 == "version:" {print $2}')
expected_tag="connectool-v${version}"

if [ "${CI_COMMIT_TAG:-}" != "$expected_tag" ]; then
  echo "release tag must be $expected_tag" >&2
  exit 1
fi

mkdir -p dist
helm lint "$chart_dir" -f "$chart_dir/examples/re8ch-values.yaml"
helm package "$chart_dir" --destination dist
printf '%s' "$CONNECTOOL_PASSWORD" | helm registry login "$registry" --username "$CONNECTOOL_USERNAME" --password-stdin
export DOCKER_CONFIG="${HOME}/.config/helm/registry"
helm push "dist/connectool-${version}.tgz" "oci://${registry}/$(dirname "$repository")"

digest=$(crane digest "${registry}/${repository}:${version}")
cosign sign --yes --identity-token "$SIGSTORE_ID_TOKEN" "${registry}/${repository}@${digest}"
cosign verify \
  --certificate-identity-regexp '^https://gitlab\.com/reachievellc/service-backend//\.gitlab-ci\.yml@refs/tags/connectool-v[0-9]+\.[0-9]+\.[0-9]+$' \
  --certificate-oidc-issuer https://gitlab.com \
  "${registry}/${repository}@${digest}" >/dev/null

oras push "${registry}/${repository}:artifacthub.io" \
  --config /dev/null:application/vnd.cncf.artifacthub.config.v1+yaml \
  "$chart_dir/artifacthub-repo.yml":application/vnd.cncf.artifacthub.repository-metadata.layer.v1.yaml

echo "published and verified ${registry}/${repository}@${digest}"
