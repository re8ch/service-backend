# Adapter

Adapter is the provider-neutral invocation layer for Nuclio/container,
Spin/WasmEdge, OpenAI-compatible model runtimes and external cloud Serverless.
It uses EndpointSlice readiness and Advanced Fabric node `uplinkMbps` when
ranking in-cluster endpoints. PublicEdge publication is an explicit, disabled-
by-default chart option.

Nuclio is dynamically addressed as
`/invoke/nuclio/<namespace>/<function>/<path>`. The adapter reads the
`NuclioFunction` status, accepts only `ready` (or explicitly allowed
`scaledToZero`) functions, resolves the function's internal invocation Service,
and then ranks its ready EndpointSlice addresses. It therefore preserves
Nuclio's build, trigger, scale-to-zero and function lifecycle while adding
fabric capacity, observed latency and in-flight-load signals. It does not
replace the Nuclio controller or mutate functions.

Some existing regional functions are represented in the central cluster by
Services rather than NuclioFunction CRs. For those, discovery is permitted only
when a Service selector or label explicitly contains
`nuclio.io/function-name=<requested-function>`; arbitrary Service-name routing
is rejected.

Routes use `/invoke/<provider-prefix>/<provider-path>`. The default release
enables `canary/*` on the local qualification provider and `openai/*` on the
existing LiteLLM Service. Authorization is forwarded; Adapter does not
own LiteLLM keys, provider credentials, tenant budgets or model policy.

Connection-level fallback is allowed only when an `Idempotency-Key` is present.
Once an upstream returns HTTP headers, Adapter streams that response and
never retries it. `X-Re8ch-Canary-Fail-Primary: true` is reserved for private
qualification and deliberately skips the first candidate.

Spin and WasmEdge are separate native engine workloads. Enabling Spin requires
an immutable engine digest and a digest-pinned Spin OCI application. Enabling
WasmEdge requires an immutable WASI HTTP image plus a working WasmEdge
RuntimeClass on eligible nodes. There is no silent Linux-container emulation.

`/metrics` exports provider/function/status request totals, transport failures,
fallbacks, scale-from-zero attempts, endpoint in-flight counts and latency EWMA.
A `VMServiceScrape` is enabled by default. Nuclio's native processor metrics and
logs remain owned by Nuclio and can continue to be scraped directly; adapter
metrics describe the additional dispatch hop without relabeling native series.
