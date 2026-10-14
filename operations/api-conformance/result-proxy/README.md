# One-time result boundary (not deployed)

This package includes the security boundary and HTTP executable of the result
proxy. It does not expose a Gateway route, change targets, or replace the current
dispatch receiver. No claim of production OIDC acceptance is made.

`go run ./cmd/result-proxy` listens on port 8080. Deployment supplies
`RESULT_OIDC_POLICY` (JSON using the Policy field names), `POD_NAMESPACE`, and
the standard Kubernetes service host/HTTPS port environment. It reads the
projected service-account CA and rotates the token on every Kubernetes request.
No service account files or production credentials are needed for unit tests.

The only endpoint is `POST /api-conformance/v1/reports/<run-id>`, with an empty
body and a Bearer OIDC token. Query strings are rejected. A global per-process
10 request/second limiter returns 429; independent replicas still share the
Kubernetes atomic claim boundary. All responses disable caching. Authentication
failure is 401; unavailable/consumed/foreign reports are uniformly 403. A key
fetch failure is 503. Errors never include upstream response bodies or tokens.

GitHub keys come only from the issuer's fixed `/.well-known/jwks` URL, with a
five-minute cache, five-second timeout, 64 KiB response bound and no redirects.
Expired cached keys are not used when refresh fails; failed refreshes are
throttled for 30 seconds. New signing keys can take up to five minutes to become
available; callers may retry authentication before any report is consumed.
The Kubernetes HTTPS client rejects redirects, validates the projected CA,
limits responses to 64 KiB and permits only named run GET/status PATCH paths.
No outbound proxy environment variables are inherited by either client.

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

- Immutable run authority snapshot and validated full sanitized report schema
  (this initial envelope deliberately excludes arbitrary text and nested data).
- Controller delivery creation/expiry, repository OIDC receiver, signed image,
  scoped RBAC/NetworkPolicy and reviewed GitOps route.
- Live wrong-repository rejection, replay and response-loss acceptance tests.

Run `go test -race ./...` and `go vet ./...`. The CI workflow runs these checks
without publishing an image or changing production.
