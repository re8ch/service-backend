# Domain releases

Deployable modules publish an immutable OCI Kustomize bundle. Product releases
also publish their own container images; third-party images are not retagged.
Release-managed objects are pinned by `sha256` digest. Human-facing
tags use the module's next codename; a SemVer value is optional metadata and is
never used by Flux to select content.

Preview the next tag:

```sh
python operations/releases/allocate_codename.py platform/advanced-fabric
```

Claiming a name changes `codenames.yaml` and therefore happens in the same signed
release PR as the digest lock. Names and digests are append-only. If a release
must be rebuilt, allocate the next revision word instead of moving an existing
tag. This repository publishes manifest bundles, while product repositories
publish product images. The dedicated
`re8ch-domain-promoter` GitHub App may only open a PR that changes the module's
release record. Flux consumes `.spec.ref.digest`, never a floating tag.

Validate a release lock and preview its exact OCIRepository/Kustomization pair:

```sh
python operations/releases/flux_oci.py releases/<module>.yaml
```

The generator enforces root ownership, digest pinning, codename image tags and
an exact GitHub OIDC signer identity. It emits YAML only, with pruning disabled;
it does not create a second live owner. Cutover must remove the old root's direct
resource inclusion in the same reviewed migration that adds this source pair.

## Coverage boundary

Unit tests cover numeric/mutable tags, unknown modules, digest pins, exact signer
matching and source isolation. Integration CI renders affected local roots.
Outstanding live smoke tests are registry signature verification, Flux source
readiness and workload health after an explicit ownership cutover. Until those
pass, the new manifests are not applied to the cluster.

## Live ownership and promotion

`active.yaml` is the reviewed list of live modules. Bootstrap selects the direct
deployable roots formerly included by `clusters/re8ch`, plus the three existing
observability child reconciliations. Nested Kustomize roots stay inside their
parent bundle: they do not gain a duplicate Flux owner. Core Flux, credentials,
and root-level infrastructure declarations remain under signed Git authority.

`domain-sources` carries the private Harbor OCI sources and independent module
reconciliations. Sources pin digests and verify the exact main-branch publishing
workflow's GitHub OIDC certificate. The SOPS-encrypted pull-only robot is not a
publishing credential. Production observability remains suspended; source
cutover must not silently lift that gate.

Main-branch changes to activated module declarations publish only affected
roots. The App opens a GitHub-signed, digest-only PR with append-only codename
claims. Protected auto-merge waits for the normal required CI checks. Changes
to source inventory, CI, schemas, or unknown risky paths fail closed to full CI.
Updating one module's release lock selects that module's roots and domain tests,
not the entire cluster. Daily/release full regression is a separate workflow.

For an explicit one-module republish, dispatch `domain-oci-release` with
`bootstrap=false` and `module=platform/adapter`. No desktop private-key handling
or administrator bypass is part of routine promotion. Registry publication is
not itself deployment: only the successful signed promotion PR changes Flux's
desired digest.

The two local Helm charts (`runtime-anatomy`, `birfucate-metering`) are packaged
with module-local GitRepository sources pinned to the publisher's signed commit.
Their chart inputs map explicitly to the corresponding deployable module; an
unrelated main commit cannot advance these chart sources after that promotion.
