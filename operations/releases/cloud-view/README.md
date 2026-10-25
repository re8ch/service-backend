# Cloud release view

A dependency-free static page reads the public GitHub REST API for
`re8ch/service-backend`. GitHub Pages publishes exactly five static files; there
is no image, OCI bundle, promotion, database, credential broker or controller.

The existing NodeContract observer workflow also collects Flux evidence every five
minutes (or manual `scope=flux`). GitHub schedules may be delayed; snapshots expire
15 minutes after observation, including suspended resources. No controller is added.
The existing OIDC observer identity can only read Flux CRs and controller Deployments
in the five declared controller namespaces. It cannot read Secrets or mutate runtime.
The existing Executor App publishes Checks using a short-lived token narrowed to
Checks write for this repository. Observer and Maintenance App permissions are unchanged.

Native Flux GitHub event statuses are useful but do not provide a complete inventory
of quiet/suspended resources or controller replicas. This bounded observation fills
that gap using the existing federation authority, not a second desired-state registry.
Nine Flux resource kinds and controller Deployments are projected through an explicit
field whitelist. Specs, environment variables, raw messages, webhook paths and Secret
contents are never published. Each resource retains its own source/chart revision;
the main commit identifies the observation code, not every autonomous domain's revision.

An in-progress inventory Check invalidates the previous batch. Only a completed
inventory referencing every domain Check ID is accepted, with matching App, commit,
batch and timestamp. Partial collection, API failure, new main without evidence,
future timestamps and expired evidence are Unknown. Resource failures remain failures;
a successful inventory Check means collection completed, not that services are healthy.
Resources outside active controller namespaces and resources without health conditions
remain Unknown (suspended objects are explicitly marked suspended while fresh).

Checks and Deployment statuses remain separate source records. Green build checks
are never interpreted as runtime health. This is trusted App observation, not a
cryptographic runtime attestation verifier or an independent workload probe.

Requests are anonymous, omit cookies, and target only api.github.com. They are
bounded to 100 statuses, 100 checks and three deployments for one immutable
revision. Refresh is manual to respect GitHub's shared-IP anonymous rate limit.
A local timer expires evidence without another API request. Only links to
https://github.com are allowed and all API text is rendered with textContent.

Run `node --test operations/releases/cloud-view/*.test.mjs`.
The standalone URL is https://re8ch.github.io/service-backend/ . Headlamp can
embed the same public page; its native Flux views remain cluster diagnostics.

Headlamp is owned by the existing main Flux operations-ui Kustomization. Its
explicit health checks cover headlamp, headlamp-gateway-proxy and
headlamp-active-elector Deployments. The existing GitHub Provider reports that
Kustomization to the same commit status API; there is no Headlamp SSA release
contract or separate promotion workflow. Native main Flux is the sole writer.

The two Headlamp-authored public catalog ConfigMaps in kube-system also follow
their authoring root into native Flux. Their separate SSA contract is removed.
Rollback changes the reviewed plugin version/digest through a signed PR while
retaining native ownership; do not reactivate the retired SSA writers.

The presentation follows the existing Headlamp Flux plugin overview: resource
cards with rings, sorting, resource tables and Flux Checks / Controllers.
Reference: https://github.com/headlamp-k8s/plugins/tree/main/flux/src/overview .
Counts include only objects reported to the selected GitHub revision, never an
invented cluster inventory. Missing fields remain unknown. Sorting and filtering operate on the fetched snapshot;
they do not issue Kubernetes requests. The static page retains its five-file
publication and requires no Headlamp plugin rebuild for layout changes.
