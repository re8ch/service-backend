# Tenant model gateway activation

This is an optional, bounded MCP inference entry point. It is **not activated by this change**: chart defaults disable it, provide no tenant bindings and reference no new credential. There is no automatic key provisioning, new tenant grant, or new LiteLLM deployment.

## Authority and native capability

The authenticated session selects the active organization; the request cannot override it. A binding locates a server-held, dedicated tenant virtual key. LiteLLM's native key/team remains authoritative for model access, expiry, rate limits and budget. The binding's `models` list is an additional approved ceiling: the catalog and chat must use its intersection with native `/v1/models`. A binding is not proof that a credential is healthy or inference is permitted.

The current gateway declaration uses native virtual keys and database-backed policy. UI Dex SSO and outbound MCP OAuth passthrough do not enable incoming GoTrue JWT inference authentication. LiteLLM documents [native virtual keys](https://docs.litellm.ai/docs/proxy/virtual_keys) and [Enterprise JWT authentication](https://docs.litellm.ai/docs/proxy/token_auth); verify the installed immutable image supports any policy setting before activation.

## Activation through reviewed configuration

1. Use the approved platform interface and its plan-before-write workflow to provision or identify a dedicated LiteLLM virtual key for the intended tenant/team. Confirm active membership in that same organization. Restrict models, inference routes, expiration and native budget/rate limits. Do not reuse master, provider, platform-admin or another tenant's key. The MCP tool does not create keys or change policy.
2. Through the approved credential system, place the key in a dedicated Kubernetes Secret in the tenant runtime namespace. Secret material stays outside Git, command arguments, logs, model prompts and MCP results. Choose an environment/key name matching `^LITELLM_TENANT_[A-Z0-9_]+_KEY$`; each tenant must use a distinct name and credential. Record owner, purpose, scope, expiry and rotation requirements without its value.
3. In a signed, reviewed deployment PR, enable `tenantPlatform.tenantAPI.modelGateway`, set the fixed `gatewayURL`, `credentialsSecret` and `bindings`. The Go tenant API must also be enabled with a verified image digest. This chart injects only each explicitly bound Secret key via `secretKeyRef` into `tenant-api`; it does not inject the Secret into `admin-api`, use whole-Secret `envFrom`, or create it.
4. Run the existing tenant Go/GitOps checks and use the existing immutable image release pipeline. After deployment, read back the effective image/configuration and verify catalog, one bounded synthetic completion, native model denial, expired/missing key denial and tenant isolation. Do not describe a merged PR as activation evidence.

Illustrative non-secret shape only; `research-fixture` is not a requested real tenant:

```yaml
tenantPlatform:
  tenantAPI:
    modelGateway:
      enabled: true
      gatewayURL: https://gateway.example.test
      credentialsSecret: research-fixture-credentials
      bindings:
        research-fixture:
          tokenEnv: LITELLM_TENANT_RESEARCH_FIXTURE_KEY
          models: [approved-model-alias]
```

The adapter accepts a fixed HTTPS gateway or an internal `*.svc.cluster.local` HTTP gateway; callers cannot supply endpoints or keys. Missing configuration fails closed. To disable, review `enabled: false` and revoke the native key through the approved platform interface. Secret rotation requires the applicable runtime restart because environment variables are loaded at startup; verify afterward. No controller or credential synchronization loop is added.

## MCP research calls

First call the catalog with the authenticated session. The JSON below is the MCP `tools/call` request body; authentication belongs to the existing connector transport, never in tool arguments:

```json
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"model_gateway_catalog","arguments":{}}}
```

Choose an actual returned model alias, then call chat. Replace `returned-model-alias` with that alias. This example contains only a synthetic interface description:

```json
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"model_gateway_chat","arguments":{"model":"returned-model-alias","messages":[{"role":"user","content":"研究记录 CASE-SYNTH-001 / RUN-SYNTH-001。以下是虚构界面，不是真实用户观察：页面显示当前组织、切换按钮和保存结果。请以首次使用者任务视角列出可能误解、失败恢复问题及待验证用例。输出保留这两个记录ID，明确标为模拟假设，不声称点击过界面。"}],"max_output_tokens":1200}}}
```

The adapter limits requests to 1–16 text messages (at least one user message), 32,768 total message bytes, one non-streaming completion, and at most 2,048 output tokens (default 1,200). Its HTTP call has a 45-second timeout, a 256 KiB response limit and no automatic retry or redirect. LiteLLM's own configured provider retries remain separate; a timeout is not proof that the upstream generated no usage. There is no browser/tool execution in this MCP chat entry point.

The caller must retain case/run IDs alongside the returned result, model and usage, even if generated prose omits or alters the IDs. Store redacted findings in the [Identity UX research Project](https://github.com/orgs/re8ch/projects/3), linked to [research PR #44](https://github.com/re8ch/identity-gitops/pull/44). Do not submit production personal data or credentials as research context.

The original identity research HTTP runner is a separate client path with its own configuration and result records. These MCP tools offer a bounded alternate inference entry point; they do not automatically run that script, migrate its credentials, or attach evidence to its output. A text completion is a synthetic walkthrough, not a browser interaction or a successful usability test. UI operations, when available, need separate recorded tool actions and observations.
