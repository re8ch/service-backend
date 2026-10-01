# Third-party MCP client compatibility

Use the public HTTPS Streamable HTTP endpoints below. Each is a distinct OAuth
protected resource; do not reuse an access token across resources.

| Tool plane | MCP URL |
| --- | --- |
| Cloud Platform | `https://tools.service.re8ch.com/cloud/mcp` |
| Cluster Infra | `https://tools.service.re8ch.com/infra/mcp` |
| Agent Orchestra | `https://tools.service.re8ch.com/orchestra/mcp` |
| Tenant | `https://tools.service.re8ch.com/tenant/mcp` |

All clients should discover the resource's OAuth metadata from the MCP
challenge, use authorization code with PKCE S256, and request a token for the
exact MCP resource URL. The tenant resource has a separate authorization server
from the platform resources. Never put a shared bearer token in a public
connector definition.

## Client setup

| Client | Setup | Authentication consideration |
| --- | --- | --- |
| Cursor | Add each URL as a remote Streamable HTTP MCP server in MCP settings. | Use its OAuth login. |
| Claude custom connector | Add each URL as a separate custom remote MCP connector. | Claude offers published identity, automatic registration, or an explicitly registered OAuth client; choose only a method supported by the RE8CH authorization server. |
| WorkBuddy | Add one remote `streamableHttp` server per connector, then optionally package a Skill. | Its automatic OAuth path requires public-client registration and PKCE, including a supported callback URI. |
| ChatGPT custom MCP app | Add the remote URL in developer mode and test privately before workspace publication. | Workspace availability and approval depend on the ChatGPT plan and admin settings. |
| Doubao / Volcengine AgentKit | Check the exact product's custom MCP service form and authentication modes before registration. | AgentKit MCP support alone does not establish that the consumer can complete this OAuth flow. |

Client references: [Cursor MCP](https://docs.cursor.com/context/model-context-protocol),
[Claude remote connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp),
[WorkBuddy connectors](https://open.workbuddy.cn/docs/connector),
[ChatGPT developer mode](https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt),
[Volcengine AgentKit MCP services](https://docs.volcengine.com/docs/agentkit/MCP_services?lang=zh).

## Verification before client testing

Run `python operations/mcp-conformance/check_public_client_discovery.py`. It
performs read-only requests and fails when a resource metadata document points
at the wrong MCP URL. Then test each client's login and a read-only tool call.
Record client version, selected authentication mode, HTTP status and a redacted
error; never record tokens or full tool responses.

As of 2026-10-02, the live Infra and Orchestra routes lacked dedicated
`tools.service.re8ch.com` routing: their resource metadata resolved to Cloud,
and unauthenticated MCP probes returned 503 and 404 respectively. The GitOps
route change in this branch addresses that declaration; live behavior still
requires a signed merge, successful CI and Flux reconciliation. Dex discovery
also returned 403 from the probing host, so OAuth login must be tested after
route reconciliation rather than presumed working.
