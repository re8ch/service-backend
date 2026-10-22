# Encrypted infrastructure contracts

Node and operations-target facts are confidential. The plaintext contract
bundle is stored only as the `INFRASTRUCTURE_CONTRACT_BUNDLE_GZ_B64` secret in
the `cluster-observation` GitHub Environment. The Kubernetes API CA is stored
separately as the `KUBE_API_CA_PEM` Environment secret.

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
