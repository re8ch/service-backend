# RE8CH MCP Registry

This is the official `modelcontextprotocol/registry` server. The 2026-08-03
release-channel image reports application version `v1.8.0` and is pinned by immutable multi-architecture digest
`sha256:1b416d...01dfc`.
with a dedicated PostgreSQL database. It is the canonical discovery API for
internal MCP servers. Harbor and Git remain the artifact/source stores; LiteLLM
remains the model routing, key, quota, and usage plane.

The first deployment is ClusterIP-only. Public publishing is intentionally
disabled until a RE8CH GitHub OAuth/OIDC application and stable external origin
are configured. Never enable anonymous publishing in production.

PostgreSQL uses a statically bound, `Retain` restricted-node-6dc3f140f9 PV at
`/var/lib/re8ch/mcp-registry-postgresql`; it is isolated from the Teable,
LiteLLM, and pg-re8ch databases.

Create the runtime Secret outside Git:

```bash
POSTGRES_PASSWORD=$(openssl rand -hex 24)
JWT_PRIVATE_KEY=$(openssl rand -hex 32)
kubectl create namespace mcp-registry --dry-run=client -o yaml | kubectl apply -f -
kubectl -n mcp-registry create secret generic mcp-registry-runtime \
  --from-literal=POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  --from-literal=DATABASE_URL="postgres://mcpregistry:${POSTGRES_PASSWORD}@postgresql:5432/mcp-registry?sslmode=disable" \
  --from-literal=JWT_PRIVATE_KEY="$JWT_PRIVATE_KEY"
kubectl apply -f registry.yaml
```

The API remains compatible with the official Registry API and `server.json`.
Claude/Codex plugin manifests, Agent Skills, KAgent resources, and Teable
installation descriptors are generated projections; they are not stored as
vendor secrets in the Registry database.
