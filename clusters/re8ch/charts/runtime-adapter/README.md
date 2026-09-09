# Runtime Adapter

Runtime Adapter is the provider-neutral invocation layer for Nuclio/container,
Spin/WasmEdge, OpenAI-compatible model runtimes and external cloud Serverless.
It uses EndpointSlice readiness and Advanced Fabric node `uplinkMbps` when
ranking in-cluster endpoints. PublicEdge publication is an explicit, disabled-
by-default chart option.

Routes use `/invoke/<provider-prefix>/<provider-path>`. The default release
enables `canary/*` on the local qualification provider and `openai/*` on the
existing LiteLLM Service. Authorization is forwarded; Runtime Adapter does not
own LiteLLM keys, provider credentials, tenant budgets or model policy.

Connection-level fallback is allowed only when an `Idempotency-Key` is present.
Once an upstream returns HTTP headers, Runtime Adapter streams that response and
never retries it. `X-Re8ch-Canary-Fail-Primary: true` is reserved for private
qualification and deliberately skips the first candidate.

Spin, vLLM and external Serverless entries are disabled until their immutable
runtime release or HTTPS endpoint and Secret reference are declared. An enabled
external URL must use HTTPS. No mutable tag, plaintext credential or silent
container fallback is accepted.

