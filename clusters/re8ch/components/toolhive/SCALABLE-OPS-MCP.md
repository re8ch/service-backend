# Scalable Ops MCP control plane

The external operations tools are an independent platform control surface. They
do not depend on tenant workloads or on every Kubernetes node being Ready.

The supported composition is:

1. Authentik owns workforce users and groups.
2. Dex is the compatibility broker for Kubernetes and services already using
   its normalized OIDC claims. Moving ToolHive upstream from Authentik to Dex is
   a separate client migration, not a prerequisite for scaling Ops MCP.
3. ToolHive owns MCP publication, OAuth, policy, filtering, aggregation, audit,
   and Redis-backed MCP sessions.
4. `ops-mcp-server-app.py` is a stateless, profile-filtered execution backend.
5. Kubernetes ServiceAccounts/RBAC and each managed service's native roles are
   the final authorization boundary.

All external backend and proxy-runner workloads run as two replicas split
across A1 and B1 with required hostname anti-affinity. vMCP reaches
the raw backend through `https://admin.re8ch.com:8443/backend/...`: ToolHive
rejects `cluster.local` MCPServerEntry URLs as SSRF protection, and this Traefik
route terminates TLS before forwarding directly to the backend Service. It does
not traverse the former 8.136 relay or a second OAuth proxy.

Use `apply-scalable-external-ops.sh` for this plane. The legacy
`apply-managed-ops.sh` also reconciles unrelated cluster services and is not the
deployment entrypoint for external Ops MCP.
