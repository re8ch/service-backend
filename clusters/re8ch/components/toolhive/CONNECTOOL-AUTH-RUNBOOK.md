# ConnecTool MCP authentication recovery runbook

This runbook recovers the production ConnecTool authentication chain when a
Codex/ChatGPT plugin has no Authenticate button, OAuth login loops, or MCP calls
return `401`/`403`. It records the first complete recovery performed on
2026-09-07 and turns the incident findings into repeatable gates.

Use this runbook for the three public MCP resources:

| Surface | MCP resource | Dex audience |
| --- | --- | --- |
| Admin | `https://admin.re8ch.com:8443/mcp` | `re8ch-platform-admin` |
| Tenant | `https://tools.re8ch.com/tenant/mcp` | `re8ch-tenant` |
| Cloud | `https://tools.re8ch.com/cloud/mcp` | `re8ch-cloud-platform-tool` |

Do not use an operational MCP to repair its own publication. Keep SSH and the
R640 Kubernetes control-plane path available as break-glass access.

## Success criteria

Recovery is complete only when all of the following are true:

1. Flux applied the exact reviewed `main` SHA.
2. Every MCP backend, proxy, vMCP and ToolHive control Pod is Running and Ready
   on A1/B1, with one replica per node for each HA component and none on R640.
3. A newly issued JWT has the exact resource audience and required `groups`.
4. Each endpoint completes `initialize -> notifications/initialized ->
   tools/list`.
5. At least one read-only `tools/call` succeeds on each required surface.
6. The admin vMCP reports both internal Ops and `admin-api-backend` tools.
7. No current vMCP log contains `invalid_token`, unsupported auth conversion,
   or unresolved tenant identity failures.

Metadata `200`, OAuth login success, Pod Ready, and a non-empty `tools/list`
are useful intermediate observations. None is sufficient on its own.

## Authentication architecture

```text
Codex / ChatGPT
    |
    | OAuth authorization code + PKCE; Dex user JWT
    v
Dex ---- GitHub team membership -> canonical groups claim
    |
    v
ToolHive vMCP ---- audience validation + Cedar authorization + user audit
    |
    | private static service header; not the user JWT
    v
admin-api ---- TenantIdentityBinding -> protected admin tenant
```

There are two intentional authentication boundaries:

- The public vMCP validates the end-user Dex JWT and applies Cedar once. The
  canonical platform administrator group is `re8ch:k8s-admins`.
- The private `admin-api` hop uses a rotatable service bearer. NetworkPolicy
  permits only the `re8ch-admin-vmcp` Pod identity to reach it. The service
  identity resolves to the protected `admin` tenant.

Do not forward an end-user JWT through this private hop unless ToolHive gains a
reviewed, supported delegation mechanism. Do not add a second interactive OAuth
flow to `admin-api`.

## Prerequisites

- GitHub permission to create and merge signed service-backend changes.
- GitHub organization permission for the canonical teams.
- Macmini workspace and SOPS age key access.
- Break-glass SSH access to `r640` with passwordless `sudo -n k3s kubectl`.
- The public authority invariant in
  `operations/network/manifests/network/public-mesh/inventory.json` has been
  read before changing any PublicEdge network state.

Start every repository change with:

```bash
python operations/workspace-controller/workspace.py doctor
git status --short
```

Use an isolated worktree at `origin/main`. Preserve unrelated user changes.
All desired-state changes require signed commits, successful CI, a GitHub
pull request, and Flux application. Direct cluster writes are limited to diagnosis,
reconcile triggers, and exact operational recovery actions.

## Fast triage

Classify the failure before changing credentials:

| Observation | Likely layer | First check |
| --- | --- | --- |
| No Authenticate button or MCP tools absent from the session | Plugin/MCP capability exposure | `codex mcp list`; restart/reconnect after server recovery |
| OAuth metadata discovery `503` | PublicEdge, route, or upstream availability | protected-resource URL and R640 route/controller health |
| OAuth completes but JWT has no groups | Dex GitHub connector/team mapping or stale token | decode claims safely; inspect reconciled Dex connector |
| MCP returns `401 invalid_token` | signature/audience, forwarded credential, literal SOPS ciphertext, or stale Secret env | JWT audience; backend/vMCP logs; Secret relationship; Pod start time |
| MCP returns `403` | Cedar or tenancy authorization | JWT groups and `TenantIdentityBinding` resolution |
| `initialize` succeeds but tools are missing | one vMCP backend was skipped or failed initialization | vMCP backend reconciler and health logs |
| `tools/list` succeeds but `tools/call` fails | runtime authorization/data boundary | perform a read-only call and inspect the direct backend log |

Never respond to a `403` by repeatedly logging in until the token claims have
been inspected. Never respond to an `invalid_token` by broadening Cedar policy.

## Procedure

### 1. Establish GitOps and placement truth

```bash
ssh r640 'sudo -n k3s kubectl -n flux-system get \
  gitrepository/service-backend kustomization/service-backend -o wide'

ssh r640 'sudo -n k3s kubectl get pods -A -l toolhive=true \
  -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name,NODE:.spec.nodeName,PHASE:.status.phase,READY:.status.containerStatuses[0].ready'
```

All MCP compute and ToolHive control Pods must resolve to `a1-wsl-zt` or
`b1-wsl-zt`; every two-replica component must have one Ready Pod on each node.
R640 may remain the public transport/control-plane access path, but must not
host MCP sessions or compute. Repair GitOps declarations instead of adding
ad-hoc scheduler patches or name-based policies.

### 2. Verify OAuth discovery independently

For each resource, request its RFC 9728 metadata and authorization-server
metadata. Confirm that the protected resource, authorization server, token
audience/client and PKCE method form a one-to-one contract. A no-token MCP
request must return an RFC 9728 bearer challenge, not a browser redirect.

If discovery returns `503`, stop here and repair PublicEdge or the upstream
service. `codex mcp logout/login` cannot repair an unavailable metadata path.

### 3. Verify Dex identity declaration

The continuously running `dex-identity-reconciler` owns the client contract and
GitHub connector mapping. Confirm it is healthy and idempotent:

```bash
ssh r640 'sudo -n k3s kubectl -n dex get deploy dex dex-identity-reconciler -o wide'
ssh r640 'sudo -n k3s kubectl -n dex logs deploy/dex-identity-reconciler --tail=30'
```

The canonical mappings are declared in
`platform/connectool/dex-client-reconciler.yaml`. The important settings are:

- `teamNameField: slug`
- `loadAllGroups: false`
- `re8ch/k8s-admins` -> `re8ch:k8s-admins`
- explicitly approved tenant organization/team mappings

The reconciler must exit without patching or restarting Dex when desired state
already matches. Do not restore one-shot reconciliation Jobs.

For an administrator, verify exact GitHub state before mutation:

```bash
gh api orgs/re8ch/teams/k8s-admins --jq '{slug,name,privacy}'
gh api orgs/re8ch/teams/k8s-admins/memberships/USERNAME --jq '{state,role}'
```

Create or change membership only with explicit authorization. Group membership
and team maintenance are separate privileges; record if GitHub makes the team
creator a maintainer.

### 4. Refresh stale OAuth credentials

Existing access tokens do not acquire newly configured group claims. After Dex
and GitHub desired state are healthy, refresh only the affected MCP credential:

```bash
/Applications/ChatGPT.app/Contents/Resources/codex mcp logout re8ch-tenant-admin
/Applications/ChatGPT.app/Contents/Resources/codex mcp login re8ch-tenant-admin \
  --scopes openid,profile,email,groups,offline_access
```

Use the corresponding server name for tenant or cloud. Complete the browser
PKCE flow, then inspect only safe JWT facts: `aud`, expiry, group count and
presence of required groups. Never print or paste the token.

Expected platform-admin facts:

```text
aud = re8ch-platform-admin
groups contains re8ch:k8s-admins
```

### 5. Diagnose the private admin backend

The admin publication aggregates `ops-admin-vmcp-internal` and
`admin-api-backend`:

```bash
ssh r640 'sudo -n k3s kubectl -n toolhive-system logs \
  deploy/re8ch-admin-vmcp --since=10m | \
  grep -E "admin-api-backend|invalid_token|unsupported auth|unhealthy|healthy"'
```

Interpret the results precisely:

- `unsupported auth type: bearerToken`: the CRD schema admitted a type that the
  deployed vMCP converter did not implement. Use the converter-supported
  `headerInjection` strategy with `Authorization` and a Secret value containing
  the complete `Bearer ...` header.
- `401 invalid_token`: compare the Secret relationship and running process
  state without displaying values. Confirm the ToolHive header equals
  `"Bearer " + admin-api token` and both namespaces contain the same token.
- `identity is not bound to an active tenant`: authentication succeeded; the
  failure is tenancy authorization. Confirm the protected `admin` Tenant and
  both `admin-vmcp-service` and `admin-github-k8s-admins` bindings.

The relevant desired state is split across:

- `clusters/re8ch/charts/birfucate-metering/templates/tenant-runtime.yaml`
- `clusters/re8ch/charts/birfucate-metering/values.yaml`
- `clusters/re8ch/secrets/admin-vmcp-backend-auth-*.sops.yaml`

### 6. Verify SOPS at rest and after Flux

Each namespace must use a standalone SOPS Secret manifest. Do not put encrypted
Secrets inside a Kubernetes `kind: List`: the 2026-09-07 incident showed Flux
applying nested `ENC[...]` values literally even though reconciliation reported
success.

Validate locally without printing plaintext:

1. Decrypt both files in memory.
2. Assert both raw tokens are identical.
3. Assert each authorization header equals `Bearer ` plus that token.
4. Assert the token has the expected non-zero length.

After Flux completes, repeat those assertions against live Secret values. A
successful SOPS command proves the file can be decrypted; it does not prove the
cluster received plaintext.

### 7. Restart consumers after Secret rotation

Secret-backed environment variables are read at process start. Updating the
Secret does not update an existing container. After a reviewed rotation:

```bash
ssh r640 'sudo -n k3s kubectl -n tenant-infrastructure rollout restart deployment/admin-api'
```

The ToolHive operator owns the vMCP Deployment and may reconcile away a manual
Deployment annotation before replacing the Pod. Verify the Pod start time. If
it did not change, resolve the exact admin vMCP Pod and recycle only that Pod:

```bash
ssh r640 'sudo -n k3s kubectl -n toolhive-system get pod \
  -l app=virtualmcpserver,toolhive-name=re8ch-admin-vmcp -o wide'
```

Delete only the verified Pod after confirming the controller has two desired
replicas, the A1/B1 affinity contract and one Ready peer on the other node.
Wait for its replacement to become Ready and confirm the client can reconnect
with a fresh in-memory MCP session.

### 8. Reconcile reviewed desired state

After CI and merge, fetch `origin/main` and record its full SHA. Trigger the
GitRepository, root Kustomization, and affected HelmRelease using Flux
reconcile annotations or the Flux CLI. Wait for completion and require the
reported source/applied revision to equal that exact SHA.

Do not accept “latest main” without comparing hashes. Do not apply rendered
business resources directly as the permanent fix.

### 9. Run protocol-level acceptance

For admin, tenant and cloud, perform:

```text
POST initialize
POST notifications/initialized
POST tools/list
POST tools/call (known read-only tool)
```

Handle both supported transport modes: tenant may be stateless and omit
`Mcp-Session-Id`; vMCP surfaces are stateful and require the returned session
header. Suitable read-only calls are:

| Surface | Read-only acceptance call |
| --- | --- |
| Admin | `admin-api-backend_tenant_list` |
| Tenant | `tenant_capabilities_list` |
| Cloud | `ops-observe-vmcp-internal_get_usage_guide` |

Evidence must include only status, tool count, required-argument list,
`isError`, content-block count, Git SHA, image digest and operation IDs. Do not
store bearer tokens or complete business responses.

## 2026-09-07 incident findings

The incident was not one OAuth bug. Four independently healthy-looking layers
combined into a failed user experience:

1. The GitHub connector did not declare the canonical administrator team, so
   newly issued tokens had no usable admin group.
2. The ToolHive CRD accepted `bearerToken`, but the deployed vMCP converter
   rejected it at runtime. Schema validation alone missed this.
3. Two encrypted Secrets were nested in `kind: List`; Flux reported success but
   stored literal SOPS ciphertext. Both ends matched the same wrong value,
   making a naive equality check misleading.
4. After authentication succeeded, the protected admin tenancy object and
   bindings were absent, turning the next failure into `403`.

The recovery merged service-backend MRs `!318` through `!322`, established the
canonical `re8ch:k8s-admins` group, split SOPS resources, used supported header
injection, declared the protected admin tenant, refreshed all three OAuth
credentials, and completed real read-only tool calls.

Final observed acceptance counts were:

- admin: 20 tools, including 8 `admin-api-backend` and 12 internal admin tools;
- tenant: 56 tools;
- cloud: 66 tools.

Counts are diagnostic snapshots, not permanent contracts. Tool identity and
successful calls matter more than an exact count.

## Rollback

Rollback must preserve the single public identity boundary.

1. Revert the offending Git commit through a signed GitHub pull request.
2. Let Flux apply the revert and verify its exact revision.
3. If rotating credentials, update both standalone SOPS Secrets atomically and
   restart both consumers.
4. Do not restore the former one-shot Dex Job, name-based scheduling policies,
   duplicate VirtualMCPServer owners, end-user token forwarding, or Authentik.
5. If public authentication is unsafe, fail the publication closed while
   retaining break-glass SSH/control-plane access.

## Escalation and unresolved external identity cases

If GitHub API shows tenant-team membership but Dex omits that external
organization's group, check the organization's OAuth/third-party application
policy and approval for the Dex GitHub application. Do not fabricate a group or
infer tenancy from email. Platform administrators can resolve through the
explicit protected-admin binding; tenant users still require their real
organization claim.

Escalate when:

- the canonical GitHub team cannot be administered;
- Dex cannot query an approved organization;
- Flux cannot decrypt standalone Secrets with the configured `sops-age` key;
- the installed ToolHive controller and CRD advertise incompatible auth
  capabilities;
- a tenant identity maps to zero or multiple active tenants;
- any MCP workload cannot run on both A1 and B1 under the declared affinity.

Attach sanitized protocol evidence, exact Git/Flux revisions, affected backend
IDs and relevant controller messages. Never attach credentials or decrypted
Secret content.

## Prevention checklist

- Test runtime auth conversion, not only CRD schema admission.
- Reject SOPS-encrypted Kubernetes `List` resources in CI.
- Test live Secret plaintext shape after Flux without revealing values.
- Restart every environment-variable consumer during credential rotation.
- Require `initialize`, `tools/list` and a read-only `tools/call` in release
  health gates.
- Test both positive and negative group/audience cases.
- Keep Dex group mappings and `TenantIdentityBinding` declarations under
  continuous reconciliation.
- Alert on skipped vMCP backends even when the aggregate remains Ready.
- Keep all ConnecTool publications, MCP backends and ToolHive controls spread
  across A1/B1; R640 is only the existing PublicEdge transport authority.
- Never describe multiple replicas on one node as high availability.
