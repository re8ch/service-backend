# Domain releases

## Current authority

GitHub Actions and the bounded cloud SSA executor are the platform delivery
writer. Kubernetes is runtime authority; an independent read-only observer
supplies health evidence after apply.

The complete ownership model is documented in
[`CLOUD-SSA-DELIVERY.md`](CLOUD-SSA-DELIVERY.md). Multi-cluster execution
follows the
[`multi-cluster federation contract`](../network/docs/multicluster-federation-contract.md).

## Multi-cluster execution profiles

Cloud SSA selects a target cluster from the reviewed ReleaseContract. It never
defaults every release to the main cluster. Each supported cluster has an
independent execution profile containing endpoint candidates, CA, protected
GitHub Environment, OIDC audience, RBAC identity and durable release state.

`re8ch` uses `kube-api.service.re8ch.com` as its stable multi-entry transport.
`re8ch-atlantic` uses `kube-api.atlantic.service.re8ch.com` and its own protected
Environment. A bundle targeting Atlantic is authenticated and applied directly
to Atlantic. The main cluster, PublicEdge and AmongCluster never proxy that
Kubernetes write.

An Active grant is authorization intent, not release evidence. Acceptance
requires the exact target and identity, exact RBAC, accepted/applied SSA state,
managed-field/read-back evidence and workload health from the same cluster.

## Immutable bundle contract

`unified_bundle.py` defines the renderer-neutral boundary. Helm and Kustomize run
before that boundary and supply rendered YAML to the same validator and
deterministic archive builder. The archive contains only `manifest.json` and
canonical `objects.json`, with exact inventory, rendered and contract digests.

The compiler rejects undeclared namespaces, GVKs and names, cluster-scoped
objects unless explicitly enabled, duplicate identities, Secret payloads and
mutable container images. It checks bounded input size and object count before
apply can become possible.

```sh
python operations/releases/unified_bundle.py \
  --contract contracts/releases/example.yaml \
  --rendered "$RUNNER_TEMP/rendered.yaml" \
  --output "$RUNNER_TEMP/release-bundle.tar.gz"
```

The command prints the payload digest and embedded manifest. Publication records
that layer digest separately from the OCI manifest digest. Signing and execution
address the immutable OCI manifest digest; the executor hashes the fetched
payload before parsing it. A tag is never executable input.

`ssa_executor.py` is the single renderer-independent runtime boundary. It:

1. validates an expiring, sequenced operation;
2. binds repository, workflow, ref, Environment and OIDC audience to the
   reviewed contract;
3. verifies the OCI digest and keyless signature;
4. performs conflict-detecting Server-Side Apply with a stable field manager;
5. records accepted inventory and the previous bundle before acknowledging
   success.

The executor has no signature-verification bypass and accepts no raw kubectl
arguments. Losing its durable state is a fail-closed recovery event; it never
authorizes inference of inventory from a new bundle or pruning live objects.

## Publication and promotion

Deployable modules publish immutable bundles. Product repositories publish their
own product images; third-party images are not retagged. Release-managed objects
are pinned by `sha256` digest. Human-facing codenames and SemVer values are
metadata only.

```sh
python operations/releases/allocate_codename.py platform/advanced-fabric
```

The `re8ch-domain-promoter` GitHub App may only open a PR that changes the
module's release record. Names and digests are append-only. Registry publication
is not deployment: only a reviewed accepted operation may advance the digest
applied by Cloud SSA.

## Verification boundary

CI covers bundle schemas, allowed resources, digest pins, signer identity,
source isolation and bounded SSA operations. Live completion requires:

- protected workflow and cluster-specific OIDC identity;
- accepted immutable artifact and exact operation envelope;
- SSA receipt and managed-field/read-back evidence;
- independent workload and functional health evidence.

A successful apply is not proof that every workload is healthy. A stale or
failed observation becomes `Unknown` rather than inheriting an earlier green
result.
