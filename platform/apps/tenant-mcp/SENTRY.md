# Tool diagnostics

The official `sentry-go` client records MCP entrypoint transactions, verified
identity resolution, organization-context RPC, tenant-control and native backend
phases. Errors and traces are fully sampled. Delivery is asynchronous; a Sentry
outage does not change a tool's result or replay its business operation.

| Environment | Purpose |
| --- | --- |
| `SENTRY_DSN` | Reviewed project's public SDK ingestion DSN; never an API token or secret DSN. |
| `SENTRY_RELEASE` | Immutable product source revision, set by the runtime release declaration. |
| `SENTRY_ENVIRONMENT` | Runtime environment, for example `production`. |

Absent or invalid DSN configuration retains local operation diagnostics and
logs that Sentry is disabled. Runtime acceptance must verify actual ingestion;
Pod readiness does not prove telemetry delivery.

MCP results add `_meta.re8ch.com/diagnostics` with `operationId`, `traceId`, and
`sentryEventId` when a failure event is captured. Protocol errors retain their
codes and messages and add these diagnostic references. Native nonzero exit
codes are reported without changing the original MCP envelope semantics.
The HTTP response also carries `X-Re8ch-Operation-Id`.

Events contain fixed tool/phase names, error categories, timings, release,
trace identifiers, and sanitized stack frames. They omit bearer credentials,
headers, request URLs, user/tenant identifiers, tool arguments, command output,
result bodies and arbitrary incoming baggage. Each request owns its diagnostic
scope, including requests sharing an initialized SDK session. A validated
Gateway trace and Gateway request identifier connect the two services.

The same trace and operation ID propagate to the configured Cloud Platform and
tenant-control origins. Identity HTTP requests also receive a fixed, sanitized
client phase. Redirects or other destinations never receive a tracing carrier;
authentication, cancellation, request budgets and no-replay semantics retain
their existing behavior.

## Metrics and logs

- `re8ch_toolchain_http_requests_total` and
  `re8ch_toolchain_http_request_duration_seconds` cover pre-tool authentication,
  MCP and internal catalog requests. Labels are `component`, `environment`,
  `phase`, `method`, `status_class`, `outcome`, and `error_category`.
- Existing `re8ch_mcp_tool_calls_total` and
  `re8ch_mcp_tool_call_duration_seconds` retain their schemas. Their outcome
  reflects the same first diagnostic failure as Sentry, including nonzero native
  command exits inside valid MCP envelopes. Histogram buckets include calls up
  to one hour.
- `re8ch_toolchain_build_info` carries `component`, `environment`, `release`, and
  `repository`. Join it on the scrape instance to identify a deployed source.
- `re8ch_mcp_sentry_configured` reports SDK configuration, not ingestion health.

Completion logs are pure JSON with `timestamp`, `traceId`, `operationId`,
`sentryEventId`, `component`, `environment`, `release`, `errorCategory`, and
`durationMs`. Request identifiers never become time-series labels. Metrics,
logs and Sentry can be correlated by service/tool, release and time window;
logs and Sentry share exact operation/trace identifiers.

Release automation uses the verified artifact source, including artifact reuse,
for the native Sentry release and GitHub commit association. Product builds do
not claim a production deployment. The actual GitOps rollout records deployment
only after runtime acceptance; repository access and PR association require the
corresponding native Sentry GitHub App installation.

Use the returned operation ID to query Sentry; correlate the first failed phase
with its transaction. Successful metadata calls or event ingestion alone do not
establish that the underlying business operation or long-call transport works.
