# Third-party MCP client compatibility

The current public HTTPS Streamable HTTP resources are:

| Resource | MCP URL | OAuth issuer | Registration |
| --- | --- | --- | --- |
| Cloud platform tools | `https://tools.service.re8ch.com/cloud/mcp` | `https://auth.re8ch.com` | No dynamic registration advertised |
| Tenant tools | `https://tools.service.re8ch.com/tenant/mcp` | `https://db-rw.re8ch.com/auth/v1` | Dynamic registration advertised |

The Cloud publication aggregates the platform observation tools and the
Cloud/Infra/Orchestra skill packages. There are no independent live `/infra/mcp`
or `/orchestra/mcp` resources. Tenant administration at
`https://admin.re8ch.com:8443/mcp` is a separate privileged publication; do not
substitute it for tenant self-service.

Every client should discover OAuth from the MCP challenge, use authorization
code with PKCE S256, and bind the token to the exact MCP resource. The two
resources have different issuers and must not share access tokens. Never put a
shared bearer token in a public connector definition.

## Claude custom connector

Add each desired MCP URL through **Customize → Connectors → Add custom
connector**. For Tenant, choose **Register automatically** and complete the
per-user login. For Cloud, neither **Register automatically** nor **Use Claude's
published identity** is established by the current Dex discovery document:
it advertises neither Dynamic Client Registration nor Client ID Metadata
Documents. **Use your own OAuth client** requires a registered callback and
client ID on the platform issuer; treat Cloud sign-in as blocked until that
contract has been tested. Claude's remote connector traffic originates from
Anthropic's cloud, so public reachability must be tested from that surface.

## WorkBuddy

The private plugin repository contains a `re8ch-tenant` WorkBuddy bundle that
points to the Tenant resource. Keep `auth_mode` absent so WorkBuddy invokes its
standard OAuth manager. Its documented flow requires dynamic registration,
PKCE S256 and a supported callback. The Tenant issuer advertises a registration
endpoint, but a successful WorkBuddy login and tool call remain to be tested.

The Cloud bundle points at the Cloud resource. WorkBuddy's automatic OAuth
flow cannot complete against the current Cloud issuer because no registration
endpoint is advertised. The existing tenant-admin bundle uses WorkBuddy's
server-side OAuth mode and requires coordination with WorkBuddy before use.

## Other clients

Cursor accepts remote Streamable HTTP MCP URLs with OAuth. ChatGPT custom MCP
apps accept remote MCP servers in developer mode, subject to workspace settings.
Doubao's direct end-user publication path is unavailable in the current
distribution catalog; Volcengine AgentKit MCP support is a different product
surface. Do not mark any of these clients as verified without a login and a
read-only tool call from the actual client.

References: [Claude remote connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp),
[WorkBuddy connectors](https://open.workbuddy.cn/docs/connector),
[Cursor MCP](https://docs.cursor.com/context/model-context-protocol),
[ChatGPT developer mode](https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt).

Run `python operations/mcp-conformance/check_public_client_discovery.py` to
check the two current resource URLs and the Tenant registration discovery.
This is read-only and does not prove client compatibility. For each client,
record its version, authentication mode, login result and one read-only tool
call, with tokens and response contents redacted.
