# Tool-chain source correlation

`sentry-code-mappings.json` contains native bulk code-mapping request bodies for
the existing `re8ch-studio` organization and `go-http` project. Apply each through
Sentry's organization `code-mappings/bulk/` API after the signed configuration PR
is merged. Read existing mappings first and preserve unrelated prefixes.

The stack roots match sanitized product frames; source roots point to their
actual GitHub paths. The verified immutable release commit, rather than the
default branch, resolves an event's deployed source. Validate the resulting
`stacktrace-link/` API response for a real frame and commit before claiming a
working source link. Native release commit/PR association and deployment records
are separate acceptance checks.

Only approved opaque release-automation credentials enter API requests through
the credential system. They never belong in this declaration or runtime Pods.
No image rebuild, custom controller or additional telemetry store is required.
