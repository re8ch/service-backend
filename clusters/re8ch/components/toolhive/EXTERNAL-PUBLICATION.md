# External plugin publication contract

The internal ToolHive surfaces use Kubernetes workload identity and are not a
public plugin endpoint. Public access for ChatGPT, Codex and other MCP clients
must use a separate `ops-observe-external` server and a dedicated public-read
tool filter.

## Required trust chain

1. `https://tools.re8ch.com:8443/mcp` is a stable, publicly reachable Universal MCP
   URL with a publicly trusted certificate.
2. `/.well-known/oauth-protected-resource/mcp` names that exact MCP URL as its
   resource and points to the approved authorization server.
3. Dex is the only OAuth/OIDC authorization server. Codex uses predefined Dex
   public clients with authorization code + PKCE S256 and `offline_access`;
   ToolHive validates the Dex token directly and never issues a second token.
4. The MCP resource server validates signature, issuer, audience, expiry and
   scopes on every call. A missing/invalid token receives `401` with a
   `WWW-Authenticate` resource-metadata challenge.
5. Each tool advertises an OAuth `securitySchemes` entry and accurate safety
   annotations. The external surface contains no write/admin tools and no
   cluster-wide topology, internal route, credential or cross-tenant data.
6. Verified token claims map to exactly one tenant policy before a tenant tool
   runs. A caller cannot select a tenant namespace in tool arguments.
7. When a public plugin portal requires it, the domain challenge, public privacy/terms/support pages,
   reviewer account, tool scan, five positive cases and three negative cases
   all pass before submission.

Official references:

- https://developers.openai.com/plugins/build/auth
- https://developers.openai.com/plugins/deploy/app-review
- https://developers.openai.com/plugins/deploy/submission

## Public-read candidate policy

Admit only tools whose outputs have been separately reviewed for public and
tenant boundaries. Start with usage/help and tenant-scoped summaries after
claim mapping exists. Exclude `get_platform_service_catalog`,
`get_platform_access_posture`, every platform-domain status tool, internal
database/artifact routes, Kubernetes resource listing, restart/scale,
provisioning, generic proxies and every credential-returning tool.

The public tool list is a versioned review snapshot. Any tool addition requires
another metadata scan, privacy review, positive/negative tests and publication
approval; changing the internal observe list must not change the public list.

## Release gates

- DNS and TLS pass for both MCP and authorization origins.
- Protected-resource and authorization-server metadata pass schema checks.
- OAuth login works with the production portal callback and PKCE.
- No-token, wrong issuer, wrong audience, expired token, missing scope and
  cross-tenant calls fail closed.
- Correct tenant read calls succeed and return no internal identifiers or
  undisclosed user data.
- Rate limits, revocation, audit, OTLP metrics and operator dashboards work.
- Domain verification and reviewer credentials are ready.

The deployed private Codex publication uses the R640-hosted `:8443` edge. Its
fixed readiness probe verifies these network and OAuth gates without depending
on unrelated worker nodes or any tenant workload.

## Single-layer identity contract

The production chain is `Codex -> Dex -> ToolHive vMCP -> Ops MCP backend`.
`re8ch-platform-tools` and `re8ch-platform-admin` are distinct Dex public
clients, so their token audiences cannot be replayed across endpoints. Dex
emits the canonical `groups` claim; ToolHive Cedar policies map the platform
administrator group to `admin`. Tenant access is resolved from immutable
`TenantIdentityBinding` records and is never inferred from a product-specific
group. Authentik is not on the request path.
