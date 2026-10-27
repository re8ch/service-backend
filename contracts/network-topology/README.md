# Restricted network topology and operator context

The public `public-mesh/inventory.json` files and fabric model sections are typed
references to `source.json`. They are not usable node inventories. Their immutable
source digest binds the Environment source and SOPS-encrypted projections. Never
replace these references with decoded data or infer routing from stale MEMORY.

## Member and App debugging

Members can inspect the versioned inventory and operator notes through the
existing authenticated platform API, without Git checkout or GitHub Secret export:

```json
{"backend":"native","toolName":"kube.resource.get","arguments":{"resource":"configmaps","namespace":"ops","name":"restricted-topology-context"}}
```

Discover `platform_tool_catalog` first and use its current schema and server-side
authorization. `data.bundle.json` contains the versioned desired public-mesh
source, runtime projection rule and contextual notes; `data.context.md` is readable
operator context. These notes are desired/operational records, not a live health
assertion. Current README guidance takes precedence over older MEMORY excerpts.

For actual mesh state, authorized operators can read the existing
`kube-system/public-mesh-podcidr` ConfigMap through the same API. The existing
private networking GitOps repository remains its owner. This migration does not
apply the historical service-backend projection over that runtime inventory.
Baseline inspection found peer-list drift while the public authority matched;
resolve routing drift separately, not as a side effect of confidentiality work.

Members with Actions write access can run bounded diagnostics:

```sh
gh workflow run node-contract-observer.yml --ref main -f scope=topology
```

The main-only `cluster-observation` Environment supplies
`NETWORK_TOPOLOGY_V1_GZ_B64`, a gzip/base64 JSON source below GitHub's 48 KiB limit.
The workflow verifies its exact digest, runs full fabric and production-fixture
regressions with captured output, and checks the exact runtime context via the
existing read-only OIDC identity. A namespaced Role grants only `get` on this one
ConfigMap, not list, Secrets, writes or arbitrary ConfigMaps. It verifies denied
anonymous and out-of-scope reads. The Executor App checks the signed source and
publishes a bounded Check; it gains no GitHub Secrets-reading permission. All
logs and artifacts in this public repository must still be treated as public.

## Custody and updates

GitHub Secrets has no plaintext export API. Authorized source edits start from
the authenticated runtime context or approved credential custody. Save any local
plaintext outside all checkouts in a private temporary directory; use
`NETWORK_TOPOLOGY_FILE=/private/path/bundle.json` for the existing fabric tools.
The loader accepts only the exact pinned bytes. A missing or stale source fails
closed; no Git-history fallback, public download or default topology is used.

For a new revision, create a new versioned Environment Secret through stdin,
update the public source digest/references and the workflow's Secret name in one
signed PR, and re-encrypt both projections with the existing SOPS age recipient.
Keep previous versions until the new PR, protected diagnostics and runtime API
acceptance pass. Never put plaintext or decrypted projections in Git, Actions
logs/artifacts, Variables, Custom Properties, comments or PR descriptions.

`restricted-topology-context.sops.yaml` is a read-only diagnostic ConfigMap owned
by the main Flux root, encrypted in Git and decrypted by existing SOPS support.
The public-mesh ConfigMap projection is also encrypted, preserving its original
kind, keys and payload; the autonomous networking owner is unchanged. Kubernetes
RBAC and the authenticated platform boundary restrict plaintext runtime reads.
No new private repository, API service, credential synchronizer or controller is
introduced. The small loader fills the native gap between write-only GitHub
Secrets, public templates and an authenticated runtime read interface.

## Validation boundaries and remaining exposure

Public CI runs reference/ciphertext checks and synthetic security tests. The
production-fixture suites explicitly run only in protected diagnostics, where
assertion output is captured to prevent topology leaks. A successful source check
is not proof of network parity or cluster health.

This change removes the full mesh matrix from its direct inventory/model copies,
the generated networking bundle, and topology excerpts from platform MEMORY and
its generator. Other manifests, runbooks, tests, provider inventories and routing
code still contain individual infrastructure facts and require later migration.
The repository stays public. Historical commits, artifacts and logs are untouched.

References:
- https://docs.github.com/en/actions/reference/security/secrets
- https://fluxcd.io/flux/components/kustomize/kustomizations/#decryption
