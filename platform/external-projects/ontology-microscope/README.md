# Ontology Microscope tenant deployment

Ontology Microscope is a normal workload owned by the `re8ch` tenant and runs
in `re8ch-ontology-microscope`. Its public Neuronpedia endpoint is routed
directly to the web application without the former Basic Auth sidecar. The
`/api/re8ch/*` paths are routed to the in-namespace adapter.

## Neuronpedia production stack

The public `HTTPRoute` and DNS-authority references resolve to the
`ontology-microscope-neuronpedia` Service after the replacement stack passed
database migration, projection, homepage, feature-page, and auth-boundary
canaries:

- Neuronpedia `v2.6.0` at upstream commit
  `599cd3f18a05912fb905643a8af2b34daf04ca1b`, with a versioned RE8CH overlay
  for the Chinese product-analysis homepage and ephemeral `/analyze` flow;
- a dedicated PostgreSQL 16 + pgvector database on a retained 20 Gi
  `rbd-r640` volume;
- the RE8CH projection/intervention and temporary product-analysis adapter from
  product revision `5dc3adcedd31bf196c4e444976c8b43128c05bf6`;
- a public Service named `ontology-microscope-neuronpedia`.
- a UIless evaluation data plane backed by the same PostgreSQL authority and a
  private Neo4j projection. It compiles 200-500 hierarchical natural-language
  cases through the tenant LiteLLM gateway, accepts human or agent runtime
  observations, computes scorecards, and exports JSONL/NDJSON training data.

Published immutable images:

- webapp: `registry.re8ch.com/re8ch/neuronpedia@sha256:45dd94ae9ce31be71090cc578f5df35214cddfbc067a4a9468e80f7ae8abaced`
- migrations: `registry.re8ch.com/re8ch/neuronpedia-migrate@sha256:457c7a883b00701480c58204a2bdef767a108f05f380153af51beb060d816a5c`
- adapter and evaluation worker: `registry.re8ch.com/re8ch/ontology-microscope-adapter@sha256:469ea1f24bb69bc0e595f1db625087ba5ebfb9ca9d001bb6e2fba74f8e9dfde2`

These manifests were exported from the verified restricted-node-6dc3f140f9 containerd content store
as compressed OCI archives and uploaded with `--preserve-digests`. This avoids
Harbor recompression and lets the runtime reuse the exact verified layers while
retaining immutable manifest references.

The upstream source remains pinned and the RE8CH UI is applied as a bounded
overlay. The adapter validates model output, retries one malformed response,
limits public concurrency/rate, and does not persist submitted text or results.
The optional out-of-band Secret `ontology-microscope-analysis` supplies key
`api-key` for the tenant LiteLLM endpoint. When absent, the analysis API returns
a redacted 503 while the homepage and built-in example remain available. No
credential values belong in Git.

The persistent `/api/re8ch/v2/*` API requires scoped bearer tokens. Their
hash-only registry and the dedicated Neo4j password are SOPS encrypted in the
cluster desired state. Raw operator tokens are kept in the macOS Keychain under
`re8ch-ontology-evaluation-admin`, `re8ch-ontology-evaluation-evaluator`, and
`re8ch-ontology-evaluation-reader`; they are never committed. The public
`/api/re8ch/analyses` endpoint remains ephemeral and unauthenticated.

Datasets explicitly published with `visibility: public` also have a read-only,
mobile-friendly report at `/api/re8ch/public/reports/<dataset-id>`. The report
supports full-text and scenario filters and exposes only the four declared
dataset exports. Tenant and private datasets return 404 from every public
report endpoint even when their UUID is known.

## Cutover and rollback

The legacy Deployment and Service remain intact for the rollback window.
Rollback is the inverse backend-only change; it does not delete PostgreSQL data
or its retained PVC.
