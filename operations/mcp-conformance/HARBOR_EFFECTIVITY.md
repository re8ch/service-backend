# Harbor supply-chain effectivity

The tenant Registry MCP is a tenant-scoped facade over two native boundaries:

- Harbor v2 API owns projects, repositories, artifacts, tags, robot ACLs, scan additions, SBOM additions, accessories and audit events.
- An isolated BuildKit worker owns builds and emits an OCI image plus mandatory SPDX SBOM and `mode=max` provenance attestations. It may invoke Cosign and then request Harbor/Trivy scanning. Harbor is not represented as an image builder.

`tenant-registry-canary.json` is the release gate. It performs OAuth/MCP discovery, exhausts tool and resource pagination, reads every resource, then exercises project/repository discovery, a real pushed build, artifact inspection, vulnerability report, SBOM, OCI accessories and audit evidence. Inputs are broker references supplied through environment variables; evidence retains hashes and operation identifiers, never credentials or response bodies.

The worker contract manifest is deliberately scaled to zero and excluded from GitOps until its image placeholder is replaced by a Cosign-verified digest. Promotion requires a successful collection run with signed evidence. The tenant MCP pod has no Kubernetes service-account token and sends only a tenant-bound opaque credential reference to the worker.
