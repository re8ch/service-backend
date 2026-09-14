# Ontology Microscope tenant deployment

Ontology Microscope is a normal workload owned by the `re8ch` tenant. Flux
deploys it into `re8ch-ontology-microscope`; it has no public route, DNS record,
platform registration, or runtime credentials.

- Product source: `https://github.com/re8ch/thiking-machine.git`
- Product revision: `32ea71dd5cd648b30fe4c94a62cc55291d1429bf`
- Image: `registry.re8ch.com/re8ch/ontology-microscope@sha256:f806538e9ec90be9cd6813db0e4b99ae5f7dc4427f91345d5afb291c5894eecf`
- Runtime: memory graph and mock reasoning
- Health endpoint: `/api/health`

The OCI image includes BuildKit SBOM and provenance attestations. Harbor owns
artifact scanning and retention. Never add a Harbor credential to this repo.
