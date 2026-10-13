# API conformance system

This controller runs bounded Schemathesis checks from reviewed
`APIConformanceTarget` declarations. Discovery never authorizes execution. A
target must pin its OpenAPI SHA-256, allowlisted hosts, operation IDs, canary
tenant, and cleanup contract before mutation testing is accepted.

The runner has no Kubernetes service-account token. It emits one sanitized JSON
line; the controller records the latest status on `APIConformanceRun` and sends
the result to the owning repository as a Check, workflow artifact, and one
rolling regression issue. Response bodies, headers, credentials, and replay
commands are never persisted.

GitHub publication is disabled fail-closed until the SOPS-managed Secret
`api-conformance-github-app` exists with `app-id` and `private-key.pem`. Each
target supplies its non-secret installation ID. The App needs Metadata and
Contents read plus Actions, Checks, and Issues write; it must not receive
Contents write or Administration.

Production mutation targets use only `schemathesis-canary*` tenants. Financial
fixtures must compensate with immutable reversal entries; they never delete
ledger history or call a real payment provider.

The initial identity, payment/Credits, and Commerce targets are committed in a
suspended state. This is intentional: the product schema commit must be merged
and deployed, the repository-scoped GitHub App installation must exist, and a
dedicated canary credential plus reviewed cleanup/reversal fixture must be
provisioned before a target may be enabled. Merely changing `suspend` is not
enough for either mutation target: validation still rejects generated writes
until `allowGeneratedMutations` and a cleanup contract are reviewed together.
