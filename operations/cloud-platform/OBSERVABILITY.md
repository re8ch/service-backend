# Python control-plane telemetry

Cloud Platform and tenant-control use the official `sentry-sdk` 2.76.0 and
`prometheus-client` 0.26.0, installed by their existing Dockerfiles. Both copy the
same small `re8ch_observability.py` module into `/app`. Cloud uses the existing
`cloud-platform-release.yml` image flow; tenant-control uses the existing
`tenant-release.yml` AI-operations image flow. Neither server is hot-reloaded
from a ConfigMap. Updating source or publishing an image is not runtime evidence.

The runtime reads `SENTRY_DSN` only from its environment. Set `SENTRY_RELEASE` to
`re8ch-cloud-platform@<exact-source-SHA>` or `tenant-control@<exact-source-SHA>` and
`SENTRY_ENVIRONMENT` to the reviewed environment. No management API credential
belongs in a runtime image, Pod, SDK configuration or test. An unset DSN disables
Sentry without changing business availability; startup emits a fixed enabled
status, and Metrics remain available independently.

Request isolation begins before authentication. Supported incoming correlation
is only a nonzero hexadecimal `sentry-trace` and an opaque `op_<32hex>` or
`op_<UUID>` operation ID. Arbitrary baggage and metadata are never passed to the
SDK. Every request samples its trace and errors at 100%, even if its parent was
unsampled. Technical health checks and Metrics scrapes are excluded from business
RED and transactions. No request flush or business retry is added.

The request-local relay carrier explicitly transfers the active trace parent to
its HTTP threads. It refreshes under the actual command span and shares a small,
locked first-error cache, so relay, command and HTTP unwind report one failure.
The MCP operation ID reaches Broker HTTP unchanged. Existing unique backend
`operationId`/audit results retain their original semantics and are linked by the
safe `backend_operation_id` span tag. Command timeouts, cancellation, streaming
and business response bodies retain their existing behavior.

SDK event processors rebuild events with an allowlist: opaque IDs, fixed
component/phase/error classifications, timings and configured release identity.
No token, cookie, header collection, body, argument, result, personal identity,
stack variable or source-context line is exported. Automatic SDK integrations
and logs are disabled; the async SDK transport handles delivery. The dedicated
`re8ch.observability` logger uses pure JSON and does not propagate to the global
logger. Correlation fields are `timestamp`, `traceId`, `operationId`,
`sentryEventId`, `release`, `component`, `environment`, `phase`, `errorCategory`
and `durationMs`.

## Metrics and release contract

Both servers expose an unauthenticated, internal `/metrics` on their existing
HTTP listener: Cloud Platform 19443, tenant-control 19447 (its Service uses 80).
Scraping and its exact network-policy allowance belong to the existing monitoring
GitOps owner. No additional collector or listener is introduced.

- `re8ch_toolchain_http_requests_total` and
  `re8ch_toolchain_http_request_duration_seconds` use only
  `component,environment,phase,method,status_class,outcome,error_category`.
- `re8ch_toolchain_build_info{component,release,repository,environment}=1` uses
  repository `re8ch/service-backend` and component `cloud-platform` or
  `tenant-control`.
- Optional detail is `re8ch_toolchain_phase_duration_seconds` and
  `re8ch_toolchain_operation_errors_total`, with fixed phase/error categories.
- Outcome is `success` or `error`; cancellation is `error_category="canceled"`.
  HTTP phases are `cloud.mcp`, `cloud.cluster_proxy`, `cloud.binding`,
  `tenant.resolve`, `tenant.api`, or `unknown`. Methods are fixed HTTP verbs or
  `OTHER`. Duration buckets include 120, 300, 600, 1800 and 3600 seconds.

Trace/operation IDs are never time-series labels. Metrics-to-Sentry navigation
uses component/environment/phase and the build-info release, pure-JSON log
correlation and time-window links. The deployed VictoriaMetrics stack does not
store exemplars; no exemplar storage is claimed or required.

Release metadata uses the pinned native Sentry action after existing image
verification, gated by `SENTRY_RELEASE_ENABLED`. The Cloud workflow checks out
the certified artifact's original source even when finalization reuses an old
build run. Metadata does not set a Sentry deployment environment or claim a
runtime rollout. The legacy tenant `reuse_image` input has no certified original
build-SHA field; that path intentionally does not misattribute an old image to
the current workflow SHA. Normal tenant builds associate their exact build SHA.
The native GitHub integration supplies commit/PR association.

Validation uses the actual SDK with an in-memory transport and a blocked async
HTTP transport, real loopback HTTP threads, concurrency/privacy sentinels,
command-timeout/cancellation checks and real HTTP authentication paths. No live
DSN, management credential or production mutation is used by tests. Runtime
acceptance must separately verify the actual image, configured release, scrape
target, and linked event/trace for the same operation.
