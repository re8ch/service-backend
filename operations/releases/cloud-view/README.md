# Cloud release view

A dependency-free static page reads the public GitHub REST API for
`re8ch/service-backend`. GitHub Pages publishes exactly four static files; there
is no image, OCI bundle, promotion, database, credential broker or controller.

The existing Flux GitHub Provider/Alert publishes reconciliation statuses.
Kustomization success is shown only for the exact selected main revision and
for 15 minutes after the GitHub event's `created_at`. Refreshing the page does
not refresh that evidence. Quiet healthy services consequently become unknown;
this is intentional until another real verification is reported. API failure,
missing evidence, future timestamps and revision mismatch are unknown.

Checks and Deployment statuses are displayed as source records, separately from
runtime evidence. A green CI check or Deployment success is never converted to
current runtime health. This first view does not attest actual image digests or
cover every autonomous domain. Flux status writers are trusted repository
integrations; this view is not a cryptographic runtime attestation verifier.

Requests are anonymous, omit cookies, and target only api.github.com. They are
bounded to 100 statuses, 100 checks and three deployments for one immutable
revision. Refresh is manual to respect GitHub's shared-IP anonymous rate limit.
A local timer expires evidence without another API request. Only links to
https://github.com are allowed and all API text is rendered with textContent.

Run `node --test operations/releases/cloud-view/model.test.mjs`.
The standalone URL is https://re8ch.github.io/service-backend/ . Headlamp can
embed the same public page; its native Flux views remain cluster diagnostics.
