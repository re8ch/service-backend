#!/usr/bin/env bash
set -euo pipefail

root=$(git rev-parse --show-toplevel)
component="$root/clusters/re8ch/components/arc-agent-runtime"
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

controller_digest=sha256:c2497a6dedaaa723322abfe9ea2f5a81b9b8fbb90b38e45d7835b6c7629a3aae
runner_digest=sha256:2ae7e43db61047aa3c4182e73c8f58ace35e796fa07d41b64fb3c39e81e1f365
controller_image=sha256:4b51a20db717b194e2f3fa96cfe2ddcb21602811f79053c75fd1a383517b9f90

controller_pull=$(helm pull \
  oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller \
  --version 0.15.1 --destination "$scratch" 2>&1)
runner_pull=$(helm pull \
  oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set \
  --version 0.15.1 --destination "$scratch" 2>&1)
grep -Fq "Digest: $controller_digest" <<<"$controller_pull"
grep -Fq "Digest: $runner_digest" <<<"$runner_pull"

helm template arc-controller \
  "$scratch/gha-runner-scale-set-controller-0.15.1.tgz" \
  --namespace arc-systems --include-crds \
  -f "$component/controller-values.yaml" \
  >"$component/controller-rendered.yaml"
sed -i.bak \
  "s|ghcr.io/actions/gha-runner-scale-set-controller:0.15.1|ghcr.io/actions/gha-runner-scale-set-controller@$controller_image|" \
  "$component/controller-rendered.yaml"
rm "$component/controller-rendered.yaml.bak"

helm template r640-agent-ephemeral \
  "$scratch/gha-runner-scale-set-0.15.1.tgz" \
  --namespace arc-runners --include-crds \
  -f "$component/runner-values.yaml" \
  >"$component/runner-rendered.yaml"

grep -Fq "gha-runner-scale-set-controller@$controller_image" "$component/controller-rendered.yaml"
grep -Fq "actions-runner@sha256:e275409999c37162649aa050ca573f03eab2eabcb3a1e7fcc47548de6b4f9690" \
  "$component/runner-rendered.yaml"
