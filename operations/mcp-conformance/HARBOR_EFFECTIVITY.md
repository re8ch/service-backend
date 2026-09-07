# Harbor supply-chain effectivity

The tenant Registry MCP is a tenant-scoped facade over two native boundaries:

- Harbor v2 API owns projects, repositories, artifacts, tags, robot ACLs, scan additions, SBOM additions, accessories and audit events.
- An isolated BuildKit worker owns builds and emits an OCI image plus mandatory SPDX SBOM and `mode=max` provenance attestations. It may invoke Cosign and then request Harbor/Trivy scanning. Harbor is not represented as an image builder.

The unified `tenant-canary.json` collection is the release gate. It validates
the single `re8ch-tenant` OAuth audience and the consolidated tenant tool
surface. Deeper supply-chain effectivity may exercise project/repository
discovery, a real pushed build, artifact inspection, vulnerability reports,
SBOM, OCI accessories and audit evidence through that same publication. Inputs
are broker references supplied through environment variables; evidence retains
hashes, poll attempt counts and operation identifiers, never credentials or
response bodies.

The worker contract manifest is deliberately scaled to zero and excluded from GitOps until its image placeholder is replaced by a Cosign-verified digest. Promotion requires a successful collection run with signed evidence. The tenant MCP pod has no Kubernetes service-account token and sends only a tenant-bound opaque credential reference to the worker.
