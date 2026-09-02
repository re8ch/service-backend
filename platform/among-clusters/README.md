# AmongClusters sovereign peering rollout

This directory consumes the independently released and signed AmongClusters
OCI artifacts. Flux must not deploy the legacy vendored charts under
`clusters/re8ch/charts` for version 0.2 or later.

## Phase 1: Hub and read-only UI

The first reconciliation pins Hub chart `0.2.1` and its runtime image by OCI
digest, stops the legacy in-cluster Agent, and sets
`migration.replaceCollaborationCRDs=true`. The migration hook emits sanitized
legacy object specifications before deleting the four experimental
`collaboration.re8ch.com` CRDs. `contracts.yaml` remains in Git as reviewable
pre-migration evidence but is deliberately absent from the Kustomization.

Headlamp keeps pod-local plugin storage. Its init containers download the
checksum-pinned Advanced Fabric `0.12.0` bundle, which no longer owns an
AmongClusters route, and the independent read-only AmongClusters `0.2.0`
bundle. No runtime Secret or remote Kubernetes operation is exposed to either
plugin.

After Flux reports the Hub ready and the new `peering.re8ch.com` CRDs exist,
submit a follow-up change setting `migration.replaceCollaborationCRDs=false`.

## Phase 2: pilot Agent

Do not restore the Agent HelmRelease until the Hub has issued a tenant-bound,
single-use invitation and the local trust domain has been approved. The Agent
must use the released OCI chart and image digests, keep its QUIC listener off by
default, and must not receive cluster-admin permissions.

## Rollback

Reverting the Phase 1 Git commit restores the previous release declarations and
Headlamp bundle selection. It does not recreate deleted legacy CRDs. Restoring
those CRDs or objects requires a separate reviewed operation using retained
export evidence; rollback must never delete newly created peering resources or
tenant data.
