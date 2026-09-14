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

- webapp: `registry.re8ch.com/re8ch/neuronpedia@sha256:4fffb48bfd442d0887ab6df4298ccc622d82af04c87c046bfd7dc899a56434f4`
- migrations: `registry.re8ch.com/re8ch/neuronpedia-migrate@sha256:457c7a883b00701480c58204a2bdef767a108f05f380153af51beb060d816a5c`
- adapter: `registry.re8ch.com/re8ch/ontology-microscope-adapter@sha256:1e62d513bd344a9d04940df2a7c693ddb34f8055e0cea57b81a7d90afc74d3d4`

These manifests were exported from the verified restricted-node-6dc3f140f9 containerd content store
as compressed OCI archives and uploaded with `--preserve-digests`. This avoids
Harbor recompression and lets the runtime reuse the exact verified layers while
retaining immutable manifest references.

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
