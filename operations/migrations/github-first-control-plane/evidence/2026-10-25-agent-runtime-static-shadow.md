# Agent-runtime static release shadow evidence

The first existing autonomous-domain static release shadow completed on
2026-10-25 (Asia/Shanghai).

- authority repository: `re8ch-ai/agent-gitops`;
- release: `agent-runtime-toolhive-operator`;
- protected environment: `release-executor-agent-runtime`;
- workflow run: `40115419539`;
- validate runner: `r640-enterprise-01`;
- compile/sign runner: `r640-enterprise-06`;
- locked source chart: `oci://ghcr.io/stacklok/toolhive/toolhive-operator@sha256:4881731dd7ceaf0fefcdecdb23b1c87d16b1f60fe8aa5ee75304fd33f4fa4d22`;
- signed bundle: `oci://ghcr.io/re8ch-ai/agent-gitops-release/agent-runtime-toolhive-operator@sha256:bc8e35a4c7f16c526fc0a65b87e5b2c6ad3e76b647bb68d885ce398815cc0e90`;
- signature verification: passed;
- runtime mutation: none.

This proves the GitHub-side compiler, protected approval, enterprise self-hosted
runner, OCI publication, keyless signing and identity-bound verification path.
It does not authorize a production ownership transfer. Server-side dry-run,
minimal RBAC, takeover and idempotent replay, health, rollback, drift recovery,
zero-old-writer and finalizer/orphan gates remain open. The live Flux
HelmRelease must stay active until those gates are evidenced in a separate PR.
