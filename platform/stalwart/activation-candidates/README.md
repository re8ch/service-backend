# Reviewed activation inputs (not deployed)

These candidates are deliberately outside every Kustomization and ReleaseContract.
They contain no credential and are not deployment authorization. The revision
placeholder is rejected by the runtime. Do not apply the file wholesale: bootstrap
configuration and steady-state management configuration have different gates.

## Owners and exact changes

| Input | Authoritative owner | Required change |
| --- | --- | --- |
| Cloud image and optional mounts | service-backend `platform/cloud-platform/runtime.yaml` | Already pinned to signed `9c373e188664449043ea68984719288d192ec383bc0ed918900205e9728f7199`; verify running imageID, all ready replicas, and probe health. |
| Three ConfigMaps | service-backend; candidate `configmaps.yaml` | Deliver reviewed objects through platform-authorized GitOps path. Existing five-object Cloud runtime SSA contract does **not** authorize ConfigMaps; do not pass these to it or broaden it implicitly. |
| Temporary and runtime Secrets | Cloud Platform credential broker | Only authority.ensure and credentials.stalwart.ensure produce them. No hand-written Secret, caller token, or recovery credential copy. |
| Native catalog publication | observability-gitops `clusters/re8ch/charts/birfucate-metering/values.yaml`, tenantAPI values | Merge #52 first, then temporarily set `stalwartBootstrapEnabled: true`. Keep `stalwartAdminEnabled: false` and `collaboration.enabled: false` in this first window. Regenerate locked snapshots and use the existing tenant runtime SSA release. |
| Isolated namespace and RBAC | observability-gitops chart `templates/collaboration.yaml` | Platform bootstrap delivers the exact rendered Namespace/Role/RoleBinding before tenant activation; runtime executor has no RBAC authority. |
| Consumable and application grant | observability-gitops chart CRDs and `tenantPlatform.tenants[].grants` | Install reviewed Collaboration enum, candidate Draft Consumable, and Suspended grant for verified Re8ch application tenant. Preserve existing grants; never target Admin membership. |
| Provider migration and OAuth compatibility | 8iops/agent-gitops | Complete migration, integrity, scoped OAuth and mail/calendar/files compatibility checks before discovery or provisioning. v0.16.18 is not accepted. |

## Minimal ordered activation

1. Wait for both existing runtime releases and read back actual tenant imageID
   `7086e02a406465b3035895898d3112c65a4ac8b74db551c1521802518a9504ab`
   and Cloud imageID above. Source revision `14dc5b1` already includes the read-only
   `action: discover`; no new image build is needed for discovery. MCP serverInfo
   `0.1.0` is not evidence of that source revision.
2. After provider v1 validation, review the exact provider origin and replace
   `REVIEWED_CONFIGURATION_COMMIT` with the already reviewed configuration commit
   when rendering the exact apply payload for review. This references the reviewed
   template revision; it does not require a self-referential commit or another
   commit merely to embed its own SHA.
   Deliver only the bootstrap ConfigMap with empty domainId. Its mounted token may
   remain absent for discovery. Publish the two bootstrap tools through the separate
   protected catalog switch; re-discover actual names and schemas before calling.
3. Call `credentials.stalwart.authority.ensure` with `{"action":"discover"}`.
   Select the intended enabled domain from its returned IDs; never infer an ID
   from an email/domain name. Review a second ConfigMap change recording that exact
   ID. Empty domainId cannot seed or revoke. Discovery cannot apply or take a hash.
4. Plan then apply `action: seed`; wait for the temporary Secret projection. Plan
   then apply `credentials.stalwart.ensure`. Keep all credential bytes inside the
   platform. Unknown outcomes stop for receipt-based recovery, never blind retry.
5. Read back sanitized Ready/owner/origin/expiry metadata. Deliver management
   origin and empty approved-plan ConfigMaps. Publish only the three exact admin
   tools with `stalwartAdminEnabled: true`; verify bounded read and non-admin denial.
   No plan/apply write is authorized by the empty approved-plans ConfigMap.
6. Plan/apply temporary authority revoke; remove its config and token-free receipt
   through reviewed platform delivery. Disable `stalwartBootstrapEnabled` again.
7. Separately activate user collaboration only after isolated-provider scoped OAuth
   compatibility evidence (before tenant grants) and
   isolated namespace/RBAC exist. Set verified origin, fixed registered clientID
   `re8ch-self-service`, and validationRef; approve Consumable and activate only the
   verified application's Suspended grant. Refresh locked chart snapshots and
   release. Final tenant self-service acceptance follows the active grant: present
   device consent to the user, then verify isolated reads and
   revocation with the acceptance runbook. Do not send mail or RSVP during read-only
   acceptance.

For all steps, refresh the authenticated platform catalog, verify the correct
organization, and use its exact plan/apply schema. Environment approval remains
human-controlled. A merged candidate, published tool name, or successful build
alone does not establish provider compatibility or tenant readiness.
