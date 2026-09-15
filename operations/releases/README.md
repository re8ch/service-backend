# Domain releases

Deployable modules publish two immutable objects: an OCI Kustomize bundle and
each referenced container image. Both are pinned by `sha256` digest. Human-facing
tags use the module's next codename; a SemVer value is optional metadata and is
never used by Flux to select content.

Preview the next tag:

```sh
python operations/releases/allocate_codename.py platform/advanced-fabric
```

Claiming a name changes `codenames.yaml` and therefore happens in the same signed
release PR as the digest lock. Names and digests are append-only. If a release
must be rebuilt, allocate the next revision word instead of moving an existing
tag. Product repositories publish and sign the OCI objects; the dedicated
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
