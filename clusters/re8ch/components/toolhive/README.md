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
different Kubernetes ServiceAccounts, KSA audiences, NetworkPolicies and
`MCPToolConfig` allowlists. Do not merge them into one virtual MCP. Generic
Kubernetes/SQL proxies and credential-returning tools are not published on any
default surface.

`proxy-scheduling-policy.yaml` is a narrowly scoped Kubernetes v1 mutating
admission policy. It pins only the three named ToolHive proxy Deployments to
`restricted-node-296f36f2fb`, where service and DNS routing are verified. This is required
because `MCPServer.spec.podTemplateSpec` customizes the backend StatefulSet,
not the separate proxy-runner Deployment. Manual Deployment patches are not
durable desired state.

## Install order

1. Restore access to the active K3s API and verify the active server topology.
   Verify the Helm-managed Advanced Fabric DNS authority before changing
   ToolHive resources, because KSA OIDC and OTLP both depend on stable cluster
   DNS.

   The verifier checks the fixed `192.0.2.149` Service IP, desired replicas and
   actual Pod Ready conditions. It requires three genuinely Ready authoritative
   DNS Pods on distinct eligible nodes.
2. Install the CRD and operator charts at the same pinned ToolHive version.
3. Run `apply-managed-ops.sh`; it syncs the reviewed Ops source ConfigMap,
   admin-only Harbor credential, common policy objects, proxy scheduling
   admission policy, and three MCPServers. It refreshes backends before
   proxy-runners so the published tool schema changes atomically for clients.
4. Apply `kagent-integration.yaml`, run the token-rotator Job once, then run
   `attach-toolhive-agents.sh` after the target Agent image is available.
5. Create the isolated Registry database/role on the existing MCP Registry
   PostgreSQL instance, apply its NetworkPolicy from `registry.yaml`, and
   install the Registry Server chart with
   `registry-values.yaml`.
6. Use `kubectl auth can-i` and MCP `tools/list` to execute the acceptance
   matrix in `PERMISSIONS.md` before connecting KAgent.

```bash
helm upgrade --install toolhive-crds \
  oci://ghcr.io/stacklok/toolhive/toolhive-operator-crds \
  --version 0.41.0

helm upgrade --install toolhive-operator \
  oci://ghcr.io/stacklok/toolhive/toolhive-operator \
  --version 0.41.0 \
  --namespace toolhive-system --create-namespace \
  --values operator-values.yaml
```

Deploy or update the managed Ops surfaces without printing credentials:

```bash
./apply-managed-ops.sh
kubectl apply -f kagent-integration.yaml
kubectl -n kagent create job --from=cronjob/toolhive-token-rotator \
  toolhive-token-rotator-bootstrap
kubectl -n kagent wait --for=condition=complete \
  job/toolhive-token-rotator-bootstrap --timeout=120s
./attach-toolhive-agents.sh
./verify-managed-ops.sh
```

Create the ToolHive Registry database Secret without checking credentials into
Git, then create the matching logical database and role through an approved DBA
session:

```bash
REGISTRY_DB_PASSWORD=$(openssl rand -hex 24)
kubectl -n toolhive-system create secret generic toolhive-registry-database \
  --from-literal=password="$REGISTRY_DB_PASSWORD" --dry-run=client -o yaml | kubectl apply -f -
unset REGISTRY_DB_PASSWORD
kubectl apply -f registry.yaml
helm upgrade --install toolhive-registry-server \
  oci://ghcr.io/stacklok/toolhive-registry-server \
  --version 1.5.0 --namespace toolhive-system \
  --values registry-values.yaml
```

The Helm-managed Registry Server is authoritative. ToolHive 0.41 deprecates
the former `MCPRegistry` CRD, so it is intentionally not used.

The registry uses a separate `toolhive_registry` database and role on the
existing `mcp-registry` PostgreSQL instance. It does not share tables or
credentials with the compatibility registry.

As of 2026-08-04, restricted-node-6dc3f140f9 reports Ready but cannot reliably create new Pod
sandboxes. ToolHive proxy-runners and the two integrated KAgent workloads run
on `restricted-node-296f36f2fb`; managed Ops backends temporarily use the cached approved Python
image on `rancher-panel`. Move them only after a fresh image-pull and Pod
sandbox smoke test passes.

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
- ToolHive emits audit logs and OTLP traces/metrics to the existing collector.
  KAgent session history remains the human tool-argument/result audit trail.
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

`external-observe-publication.template.yaml` records the OIDC and RFC 9728
fields for a future, separate read-only public surface. It is fully commented,
so accidental `kubectl apply` cannot alter the working internal identities.
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
tests. See `EXTERNAL-PUBLICATION.md`.

Run the non-mutating readiness checker at any time:

```bash
./verify-external-publication-readiness.sh
```

Exit `0` means the network/OAuth discovery gates visible to the checker pass;
exit `2` means publication remains safely blocked. The script never creates an
Ingress, Secret, OAuth client, DNS record, or plugin submission.
