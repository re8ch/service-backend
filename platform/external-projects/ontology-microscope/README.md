# Ontology Microscope tenant deployment

Ontology Microscope is a normal workload owned by the `re8ch` tenant and runs
in `re8ch-ontology-microscope`. The public endpoint remains protected by the
existing `ontology-microscope-basic-auth` Secret; credential material is never
stored in Git.

## Neuronpedia canary

The first rollout deliberately leaves the public `HTTPRoute` on the legacy
service while Flux creates and verifies the replacement stack:

- unmodified Neuronpedia `v2.6.0` UI at upstream commit
  `599cd3f18a05912fb905643a8af2b34daf04ca1b`;
- a dedicated PostgreSQL 16 + pgvector database on a retained 20 Gi
  `rbd-r640` volume;
- the RE8CH projection/intervention adapter from product revision
  `3a80110c2436d26cf0c33d15a8b122f48c39fa3b`;
- an internal authenticated canary Service named
  `ontology-microscope-neuronpedia`.

Published immutable images:

- webapp: `registry.re8ch.com/re8ch/neuronpedia@sha256:5b09f5a43e91c9402abd1b6f4c93cd97ee8e6d6c354afa86d1accc7f51bed6e3`
- migrations: `registry.re8ch.com/re8ch/neuronpedia-migrate@sha256:37141efee09a7e6b63bc1d4833d2023c5e7cfe837891d9c73c8062f8a973f08c`
- adapter: `registry.re8ch.com/re8ch/ontology-microscope-adapter@sha256:0797f9bb1254ca3f05c0d24583d5177c0f11691b7fc9f297657bacdf8cdf7584`

These manifests preserve the builder layer digests (`--preserve-digests` and
uncompressed OCI layers). This avoids a Harbor recompression path that made
restricted-node-6dc3f140f9 pull a second, registry-specific copy of the same content while retaining
immutable manifest references.

The Neuronpedia build has no upstream UI source changes. It applies an exact
`maplibre-gl 6.9.0` dependency override for `GHSA-jrc7-96c5-q579` and fails on
critical production dependency advisories. The database password and
NextAuth secret are SOPS-encrypted in the cluster overlay.

## Cutover and rollback

After PostgreSQL migration, adapter projection, Neuronpedia API, authenticated
Service, and feature-page canaries pass, a second PR changes only the
`HTTPRoute` and DNS-authority backend references to
`ontology-microscope-neuronpedia`. The legacy Deployment and Service remain
intact for the rollback window. Rollback is the inverse backend-only change;
it does not delete PostgreSQL data or its retained PVC.
