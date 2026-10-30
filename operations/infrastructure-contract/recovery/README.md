# One-time infrastructure projection recovery

This isolated candidate is not imported by any normal release. It repairs only
the two missing Cloud Platform configuration Secrets. It neither restores Flux
controllers nor changes the observer or release executor identities.

## Authority and supply-chain evidence

Current `.sops.yaml`, infrastructure projections and `activate.py` use the same
reviewed age recipient. The projections were approved in #2136 on 2026-10-22;
`contracts/infrastructure/README.md` continues to declare SOPS ciphertext and the
protected Environment bundle as the source. The live `flux-system/sops-age`
metadata matches the original trust-chain creation date, with key name
`age.agekey`. No revocation was found. Controller retirement does not revoke the
retained decryption key. No key value was inspected by the agent.

The fixed official SOPS v3.13.1 image is
`ghcr.io/getsops/sops@sha256:320f253aced1393537b1e90c77eb48295204d805d4c68933264cd1285192465d`.
An actual `cosign verify` succeeded with exact certificate identity
`https://github.com/getsops/sops/.github/workflows/release.yml@refs/tags/v3.13.1`
and issuer `https://token.actions.githubusercontent.com`, including transparency
log and CA verification. This is an existing release, not a custom build. Its
Debian image contains Python through the official Azure CLI tool dependencies.
No runtime package download is allowed. A runtime dependency failure is closed.

## Boundaries

- Source is the two encrypted documents in the reviewed
  `clusters/re8ch/secrets/ai-operations-inventory.sops.yaml`. Canonical ciphertext
  hashes are pinned in `restore.py`; rendered ConfigMap contains ciphertext only.
- Exact source digest is `sha256:99c7f741aec926fe7e3839adf488d58c6fb7522d73b42fbd820a9bdbf8fbdc64`.
  Names, namespace, key sets, recipient and SOPS MAC must all match. A temporary
  random challenge encrypted to the one approved recipient and decrypted using
  the mounted identity additionally proves key possession; no custom crypto.
- Only `ai-operations/ai-operations-ssh-config` (ssh-config/byoc-known-hosts) and
  `ai-operations/infrastructure-contracts` (compat-nodes.yaml) may be patched.
- The temporary ServiceAccount has **patch only**, with two resourceNames. It has
  no Secret get/list/create/delete authority. The platform creates the exact empty
  reservations separately, using create semantics that reject existing objects.
- The reviewed payload embeds observed UID/resourceVersion. Every JSON patch tests
  UID, version and task owner before adding data; metadata is not replaced. A stale
  or unrelated object cannot be overwritten. Do not refresh receipts and retry
  after an unknown result without inspecting recovery evidence.
- The init container alone mounts the age key and has no ServiceAccount token.
  The patch container alone mounts the scoped token and has no age key. Both use
  the same verified official SOPS image and its existing Python interpreter.
- All decryption and response verification remain in memory; scratch storage is
  tmpfs, key projection is read-only, root filesystem is read-only. API responses
  and subprocess stderr are never printed. No public artifact contains plaintext.
  The network policy permits only kube-apiserver egress, with no DNS/general egress.

## Reviewed execution

1. Verify both destinations are absent using platform metadata-only inspection.
   Review then **create** `reservations.yaml`; never apply it over existing Secrets.
   Existing Cloud mounts require exact keys, so these empty reservations cannot
   make a new Pod Ready with placeholder configuration. Never add placeholder data.
   Read back only name/UID/resourceVersion/owner plus empty-data boolean. If either
   exists already, stop and inspect ownership without overwriting it.
2. Save the two non-secret UID/version receipts to an external JSON file keyed by
   exact target names. Render `python operations/infrastructure-contract/recovery/render.py RECEIPTS`
   and review the exact output against this signed revision. Default checked-in
   `candidate.yaml` deliberately has unusable placeholders.
3. Through the authorized platform path, install the fixed temporary SA, Role,
   RoleBinding, immutable ciphertext/script ConfigMap, API-only policy and dry-run
   Job. Inspect only its bounded JSON result. Both Secrets must pass server-side
   dry-run with exact data match before any actual write. No native retry occurs.
4. Review the same rendered payload with `--apply`; only the Job name/argument
   differs. Create the apply Job once after dry-run success. It again preflights
   both targets, then performs two CAS patches and validates the returned data
   internally. Output is only opaque refs, content hashes and matching booleans.
   Partial failure stops; it must not become an automatic second issuance/replay.
5. Confirm refs/source annotations and boolean completeness via the platform,
   then verify the previously blocked Cloud Platform rollout. Clear the SSA block
   only through the separate reviewed reset workflow; reuse the signed bundle.
6. Delete this task's Jobs/ConfigMap/SA/Role/RoleBinding/policy after verified
   recovery. Retain both restored Secrets and the original age Secret. Do not
   publish raw logs, decrypted manifests, service-account tokens or key material.

Validation includes negative identity/source/key/CAS tests, no-write preflight
failure, output redaction, API-only policy, immutable image pin and a local SOPS
roundtrip with a newly generated disposable fixture key (never the cluster key).
