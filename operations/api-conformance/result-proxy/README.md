# One-time result boundary (not deployed)

This package is the first, independently testable layer of the result proxy.
It does not start a server, expose a Gateway route, change targets, or replace
the current dispatch receiver. No claim of production OIDC acceptance is made.

## Trust and delivery contract

`Verify` requires a trusted, independently retrieved GitHub RSA key and pins
audience, immutable repository/owner IDs, repository name, branch, workflow path
and workflow commit. Only workflow_dispatch is accepted. Both GitHub subject
formats are supported, but both require matching immutable IDs. Environment and
pull-request subjects are not accepted. Token lifetime is at most ten minutes.

The controller must prepare `status.delivery` on a completed APIConformanceRun:
`repositoryId`, `ownerId`, `expiresAt`, explicit `claimed: false`, and the typed
`report` envelope. These must derive from the immutable run snapshot, never an
untrusted dispatch input or a subsequently edited target. Only the controller
may initialize delivery; the proxy may read runs and patch their status.

After verification, `Redeem` checks repository ownership and expiry, projects
the typed summary, then atomically consumes delivery before returning bytes.
The Kubernetes adapter tests resourceVersion AND claimed=false in one status
JSON patch. Conflicts are terminal, not retried. Concurrent requests and multiple
replicas cannot redeem the same version twice. A response lost after consumption
requires an explicitly authorized new delivery/run; the server must not silently
reset claimed=false. Persisted claim audit data is a SHA-256 fingerprint, not a
token. No business API credentials are available through this boundary.

## Remaining integration before enablement

- Bounded HTTPS transports for fixed GitHub JWKS and Kubernetes, TLS validation,
  no redirects, key refresh/cache, generic errors and no sensitive access logs.
- HTTP handler calling Verify before Redeem, request limits and rate limiting.
- Immutable run authority snapshot and validated full sanitized report schema
  (this initial envelope deliberately excludes arbitrary text and nested data).
- Controller delivery creation/expiry, repository OIDC receiver, signed image,
  scoped RBAC/NetworkPolicy and reviewed GitOps route.
- Live wrong-repository rejection, replay and response-loss acceptance tests.

Run `go test -race ./...` and `go vet ./...`. The CI workflow runs these checks
without publishing an image or changing production.
