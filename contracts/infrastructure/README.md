# Encrypted infrastructure contracts

Node and operations-target facts are confidential. The plaintext contract
bundle is stored only as the `INFRASTRUCTURE_CONTRACT_BUNDLE_GZ_B64` secret in
the `cluster-observation` GitHub Environment. The Kubernetes API CA is stored
separately as the `KUBE_API_CA_PEM` Environment secret.

## Versioned loading and member/App diagnostics

The existing source digest on the three encrypted projections is the immutable
version pin. `operations/infrastructure-contract/bundle.py` verifies the
Environment bundle against all three pins before materialization, rejects extra
files, links, duplicates and oversized archives, and runs the compiler's render
and check operations. Plaintext is written only outside the checkout with private
permissions. Schema errors never print source documents to public logs.

Members with Actions write access can validate the current configuration using:

```sh
gh workflow run node-contract-observer.yml --ref main -f scope=config
```

The job consumes the existing protected Environment Secret. The Executor App
then verifies access to the signed template revision and publishes only a bounded
validation Check. Apps with existing Actions write authorization may dispatch
the same workflow; Observer/Executor Apps do not gain Actions write or permission
to retrieve secret values. This is a validation/debug operation, not a Secret
download API. Full runtime inspection remains behind existing platform/Kubernetes
authentication. Do not expose a public artifact to transfer configuration to a
member; use the approved credential custody path for authorized source edits.

Run `scope=nodes` separately for live parity. A successful configuration-only
validation does not prove live parity or direct App access to Kubernetes.
The legacy secret remains the source; no private configuration repository or new
configuration service is introduced by this pilot. History cleanup is deferred.
The node observer now obtains its API server, OIDC audience host and expected
node count from this verified source instead of hardcoded workflow values.

Git contains schemas, the deterministic compiler, and SOPS-encrypted Secret
projections only:

- `clusters/re8ch/secrets/infrastructure-contracts.sops.yaml`
- `clusters/re8ch/secrets/cluster-node-inventory.sops.yaml`
- `clusters/re8ch/secrets/ai-operations-inventory.sops.yaml`

The three projections carry the same public source digest. Never add plaintext
NodeContract, compatibility inventory, node address, Kubernetes UID, SSH target,
or CA files to this directory. `render` and `check` require
`INFRASTRUCTURE_CONTRACT_DIR` and `INFRASTRUCTURE_OUTPUT_DIR`; both must point to
ephemeral private storage.

## Rotation and publication

1. Decode `INFRASTRUCTURE_CONTRACT_BUNDLE_GZ_B64` into a private temporary
   directory and edit the four bundle members: `cluster.yaml`, `nodes.yaml`,
   `targets.yaml`, and `observation-policy.yaml`.
2. Run `render`, then `check`, with both compiler directory environment
   variables set to private temporary paths. Review the output locally; never
   redirect it into a tracked path or a workflow log.
3. Convert the generated ConfigMaps to Secrets, encrypt every `stringData`
   value with the repository SOPS age recipient, and replace the three tracked
   encrypted projections. Preserve the compiler source-digest annotation.
4. Replace the Environment secret bundle with the same four source files.
   `cluster.yaml` must reference that secret and `KUBE_API_CA_PEM`; it must not
   reference a plaintext repository path.
5. Run `verify-encrypted`, schema tests, domain bundle compilation, Kustomize
   rendering, and the repository secret scanner before opening the PR.

Only the merged Git revision is eligible for Flux application. Publishing the
Environment secret or SOPS ciphertext does not authorize a host restart or a
cluster write; the A1 authentication/TLS activation remains a separately
approved maintenance-window operation with an etcd snapshot and local rollback.
