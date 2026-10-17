#!/usr/bin/env bash
set -euo pipefail

# The SOPS age key cannot encrypt itself. Copy the two existing bootstrap
# Secrets through the audited cluster control path after the namespaces exist.
for namespace in flux-agent-system flux-identity-system flux-observability-system flux-network-system; do
  kubectl -n flux-system get secret sops-age -o json |
    jq --arg namespace "$namespace" '.metadata.namespace=$namespace | del(.metadata.uid,.metadata.resourceVersion,.metadata.creationTimestamp,.metadata.managedFields,.metadata.ownerReferences)' |
    kubectl apply -f -
  kubectl -n flux-system get secret gitops-oci-reader -o json |
    jq --arg namespace "$namespace" '.metadata.namespace=$namespace | del(.metadata.uid,.metadata.resourceVersion,.metadata.creationTimestamp,.metadata.managedFields,.metadata.ownerReferences)' |
    kubectl apply -f -
done
