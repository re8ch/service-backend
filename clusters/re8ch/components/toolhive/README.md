# RE8CH ToolHive control plane

ToolHive is the final publication and runtime-governance plane for MCP tools.
The upstream MCP Registry remains the compatibility discovery API; it is not
the authorization boundary. Harbor stores OCI artifacts and Git stores the
reviewed desired state.

## Publication surfaces

| Surface | Intended principals | Included capabilities | Human GUI |
| --- | --- | --- | --- |
| `platform-observe` | all approved internal agents and tenant viewers | inventory, topology, health, metrics, registry metadata | Headlamp, Grafana, Harbor |
| `platform-operate` | tenant operator agents and operator OIDC groups | observe plus bounded restart | Headlamp and Grafana audit |
| `platform-admin` | platform-admin agents after explicit approval | identity, tenant, Harbor and Pigsty provisioning | Headlamp, Harbor, CloudBeaver, Grafana |

The three surfaces run isolated ToolHive-managed `MCPServer` backends with
different Kubernetes ServiceAccounts, NetworkPolicies and `MCPToolConfig`
allowlists. ConnecTool aggregates only the explicitly reviewed backends into
the cloud, admin and tenant vMCPs. Generic Kubernetes/SQL proxies and
credential-returning tools are not published on a default surface.

ConnecTool owns the label-scoped runtime policy for ToolHive-generated child
resources. Every `toolhive=true` workload is restricted to `restricted-node-1eb0570f74` and
`restricted-node-296f36f2fb`, spread one replica per hostname, given bounded rollout settings,
and exposed through Services that prefer same-node endpoints. Each public vMCP
and each private backend/proxy has two replicas. Memory-backed sessions use
`ClientIP` affinity; clients reconnect after a node failure instead of making a
shared Redis instance part of the availability path.

restricted-node-6dc3f140f9 remains the PublicEdge transport authority, but it is not an MCP compute,
control, or session-state node. The direct external and managed MCPServer
generations are retired; ConnecTool vMCP is the only public MCP publication.

Keep SSH and the Kubernetes control-plane path available as independent
break-glass access. Operational MCP must not be the only way to repair its own
publication or scheduling path.

For OAuth failures, missing plugin authentication, `401`/`403` separation,
SOPS verification, Secret rotation and protocol-level acceptance, follow
[`CONNECTOOL-AUTH-RUNBOOK.md`](CONNECTOOL-AUTH-RUNBOOK.md). It records the
complete 2026-09-07 recovery and the mandatory prevention gates.

## Install order

1. Restore access to the active K3s API and verify the active server topology.
   Verify the Helm-managed Advanced Fabric DNS authority before changing
   ToolHive resources, because KSA OIDC and OTLP both depend on stable cluster
   DNS.

   The verifier checks the fixed `192.0.2.149` Service IP, desired replicas and
   actual Pod Ready conditions. It requires three genuinely Ready authoritative
   DNS Pods on distinct eligible nodes.
2. Ensure the ToolHive CRDs are at the pinned version. Flux adopts and manages
   the existing `toolhive-operator` Helm release from `operator-release.yaml`.
3. Reconcile ConnecTool and the three private MCPServers through Flux. The
   signed chart supplies vMCP placement, session, PDB and child-runtime policy.
4. Verify A1/B1 placement and public OAuth initialization before retiring a
   previous chart or workload generation.
5. Create the isolated Registry database/role on the existing MCP Registry
   PostgreSQL instance. Flux manages its NetworkPolicy and Registry Server
   release through ConnecTool.
6. Use `kubectl auth can-i` and MCP `tools/list` to execute the acceptance
   matrix in `PERMISSIONS.md`.

```bash
helm upgrade --install toolhive-crds \
  oci://ghcr.io/stacklok/toolhive/toolhive-operator-crds \
  --version 0.41.0

# The operator itself is reconciled by Flux from operator-release.yaml.
```

After Flux source, root Kustomization and ConnecTool HelmRelease reconciliation,
verify that every MCP backend, proxy and vMCP has one Ready replica on A1 and
one on B1. Any restricted-node-6dc3f140f9 MCP Pod, Pending replica, Redis dependency, downstream 401,
or public 503 fails the rollout. Pod readiness alone is insufficient; complete
OAuth `initialize` and `tools/list` checks for all three public endpoints.

Create the ToolHive Registry database Secret without checking credentials into
Git, then create the matching logical database and role through an approved DBA
session. Do not install a second manual Registry Helm release:

```bash
REGISTRY_DB_PASSWORD=$(openssl rand -hex 24)
kubectl -n toolhive-system create secret generic toolhive-registry-database \
  --from-literal=password="$REGISTRY_DB_PASSWORD" --dry-run=client -o yaml | kubectl apply -f -
unset REGISTRY_DB_PASSWORD
kubectl apply -f registry.yaml
```

The Helm-managed Registry Server is authoritative. ToolHive 0.41 deprecates
the former `MCPRegistry` CRD, so it is intentionally not used.

The registry uses a separate `toolhive-registry` database and role on the
existing `mcp-registry` PostgreSQL instance. It does not share tables or
credentials with the compatibility registry.

The Registry currently uses anonymous authentication only on its ClusterIP.
Never expose it through an Ingress until Dex/OIDC scopes are configured.

`operator-values.yaml` deliberately uses namespace-scoped operator RBAC. Add a
namespace only after its tools have a reviewed permission profile. Cluster-wide
ToolHive operator permission would make publication review ineffective.

## Human tracking and audit

- Headlamp identities are declared in `human-gui-access.yaml`. Generate
  short-lived login tokens with `kubectl -n headlamp create token
  platform-observer` or `platform-operator`; neither identity can read Secrets.
- Grafana dashboard UID is `toolhive-mcp-control-plane`; the tracked URL is
  `/grafana/d/toolhive-mcp-control-plane/toolhive-mcp-control-plane`.
- VictoriaMetrics scrapes all three proxy `/metrics` endpoints through the
  `toolhive-mcp-control-plane` job in the platform observability stack.
- `panel-re8ch-vmalert` evaluates `Re8chToolHiveMCPProxyDown` and
  `Re8chToolHiveToolCallErrors`; the first covers missing proxy targets and the
  second is limited to non-2xx `tools/call` traffic so unsupported MCP
  notifications do not create false tool-failure alerts.
- ToolHive emits audit logs for MCP requests. Registry OTLP remains disabled
  until a stable in-cluster collector Service is declared.
- CloudBeaver remains a human SQL GUI and must use database roles, never the
  Ops MCP admin credential.

`SERVICE-ACCESS-CATALOG.md` maps every discovered service domain to its data or
control plane, Agent observe/operate/admin boundary, downstream native authority,
and human evidence GUI. `get_platform_service_catalog` supplies the live service
inventory; `get_platform_access_posture` reports RBAC, ServiceAccount token,
NetworkPolicy and external-exposure review signals without reading Secrets.

## External Dex-authenticated publication

Internal KSA-backed access remains live. The separate public-read canary is now
available at `https://tools.re8ch.com:8443/mcp`; it uses Dex authorization code
with PKCE S256, RFC 9728 discovery, exact audience/resource binding and a
dedicated ToolHive allowlist. The separately scoped admin MCP endpoint uses its
own Dex public client and audience.
Human visits to `https://tools.re8ch.com/` reach Stacklok's official Apache-2.0
ToolHive Cloud UI. The catalog UI is not an additional identity authority and
does not turn the registry catalog into an MCP session.
The `/mcp`
resource intentionally returns an RFC 9728 bearer
challenge when no token is supplied; MCP clients use that challenge to start
their OAuth flow and must not receive a browser-login redirect there.

Authentik is retired at zero replicas and is not on the production request
path. Its Secrets and PVCs are retained only as rollback data. The legacy
bootstrap requires an explicit `ALLOW_RETIRED_AUTHENTIK_ROLLBACK=true` opt-in;
ordinary apply and verification flows must use Dex only.

The current OpenAI publication model is a universal plugin shared by ChatGPT
and Codex. The external endpoint must therefore be a separate, public-read
surface rather than the internal observe allowlist. It needs RFC 9728 resource
metadata, OAuth 2.1 authorization code + PKCE, exact resource/audience binding,
per-tool OAuth security schemes, domain verification, public legal/support
pages, reviewer credentials without MFA, and five positive plus three negative
tests.

Run the non-mutating readiness checker at any time:

```bash
./verify-external-publication-readiness.sh
```

Exit `0` means the network/OAuth discovery gates visible to the checker pass;
exit `2` means publication remains safely blocked. The script never creates an
Ingress, Secret, OAuth client, DNS record, or plugin submission.
