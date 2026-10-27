# Self-service delivery through native Flux

> Migration direction superseded on 2026-10-27 by
> [cloud execution and local Flux retirement](github-ops-cutover-review.md).
> Existing native owners below describe the current runtime, not the target.
> Do not migrate additional SSA units back to Flux. Retain each current owner
> only until its cloud replacement has been verified without a second writer.

The delivery target is a reviewed static declaration, one active reconciler,
and the actual Flux API status visible through the plugin, Flux CLI and Headlamp.
GitHub coordinates authorization and source changes; it does not reconcile
Kubernetes. A configuration-only change must not build an image or OCI bundle.

## First production slice: observability

The authority is `operations/flux-federation/authority.yaml`:
`siren-observer/observability-gitops`, root `clusters/re8ch`,
`GitRepository/domain` and `Kustomization/domain` in
`flux-observability-system`. That existing controller already provides real
inventory for `flux tree`. Do not edit the frozen `platform/observability`
copy here and expect production to change.

The Re8ch platform catalog already exposes audited native Kubernetes operations
and reviewed dynamic workflow publication. Reuse these for the administrator
pilot; no new gateway binary, Python planner, generic shell tool or controller
is required. The reviewed procedure is
[`self-service-flux.md`](workflows/self-service-flux.md).
Publish its exact merged contents with `dynamic_skill_publish`, then read it
back with `dynamic_skill_search` and `dynamic_skill_load`. Workflow publication
does not grant runtime or GitHub permissions.

On 2026-10-25, observability PR #32 corrected OpenCost's VMAgent affinity in
the active repository. Its obsolete `zan/suxunji` placement left the scraper
Pending, the dedicated metrics database empty, and OpenCost crash-looping.
The correction already existed in the inactive service-backend copy. This is
evidence that a green check on the wrong declaration is not delivery.

## End-to-end contract

1. Resolve the authenticated organization and active membership. Re8ch Admin
   platform authority and Re8ch application membership are separate. Select the
   owner from the existing authority ledger and live Flux source, not a path
   name or an agent's remembered checkout.
2. For a new service, add its digest-pinned Kubernetes declarations to the
   owner's existing Kustomize root. Reuse its signed Git source and controller.
   Use a separate Kustomization only for a real lifecycle/permission boundary.
   Include namespace, quota, execution ServiceAccount/RBAC, Secret references,
   readiness and deletion behavior in the same review. Provision secret values
   only through the applicable tenant/platform credential workflow.
3. Build only when product source/build inputs change. Record and reuse the
   immutable image digest. Configuration changes are ordinary Git changes;
   do not wrap an existing YAML tree in a second release bundle.
4. Open a signed PR and pass the repository's checks. Use scoped GitHub App
   automation where installed; `gh` is only the client. The current protected
   Maintenance App merge workflow is scoped to `re8ch/service-backend`; it must
   not be reused for another repository by widening its token or bypassing rules.
   Until that owner's App installation is configured, an authorized human merge
   remains explicit and auditable.
5. After merge, the existing Flux Git source verifies HEAD. Prefer its existing
   Receiver/App subscription where available; otherwise its configured source
   interval remains functional. An authorized plugin reconcile request only
   writes `reconcile.fluxcd.io/requestedAt` to the existing source/reconciler.
   It never changes source URL, ref, suspend state, spec, credentials or status.
6. Verify source signature and exact merged revision, reconciler generation and
   revision, workload rollout and service health. `Ready` on a root with
   `wait: false` is not proof that every child service is healthy. Report those
   states separately. No synthetic Ready status or duplicate status controller.
7. Roll back through a signed revert PR and verify the same chain. Preserve PVCs
   and business data unless deletion is explicitly in the reviewed scope.

## Remaining implementation work and acceptance gates

| Slice | Smallest change | Acceptance |
| --- | --- | --- |
| Platform self-service | Publish the reviewed workflow using existing Re8ch admin tools | Search/load works; authorized reconcile has an audit reference; revision and live health agree |
| Ordinary tenant self-service | Add narrowly scoped status/reconcile endpoints to the existing tenant gateway, backed by reviewed service ownership | Active organization, Ready owned namespace and target ownership enforced server-side; negative cross-tenant tests; no arbitrary kubectl |
| New tenant service registration | Existing GitHub App creates a signed declaration PR in the chosen owner repository | Checks/Rulesets enforce approval; one runtime writer; fresh service appears in real Flux inventory and becomes healthy |
| Main-domain static units | Audit the 17 current SSA units one owner at a time; transfer suitable units to existing native Flux reconcilers | Disable the old writer before enabling the new one; compare inventory; verify get/tree/reconcile and rollback before deleting old bundle/promotion paths |
| Legacy inventory | Remove obsolete suspended owners only after proving no active inventory or rollback dependency remains | Root tree has no dangling references, including the deleted connectool HelmRelease |
| GitHub reactions | Reuse the owner's Receiver/Provider/Alert and actual installed App permissions | Signed merge triggers convergence and reports real deployment status; no duplicate hooks or credential-sync service |

The ordinary tenant endpoints are not implemented by publishing a workflow.
The current tenant workload API does not allow Flux CRs, and the main Flux
controller watches only its own namespace. A tenant cannot turn this procedure
into administrator access. New tenant registration must establish an explicit
watched reconciler and bounded execution identity before advertising availability.

Native documentation:
[reconcile requests](https://fluxcd.io/flux/components/kustomize/kustomizations/#triggering-a-reconcile),
[multi-tenancy](https://fluxcd.io/flux/installation/configuration/multitenancy/),
[Flux tree](https://fluxcd.io/flux/cmd/flux_tree_kustomization/).

## Main-domain native pilot: supabase-rest

`clusters/re8ch/components/supabase-pg-re8ch/supabase-rest-runtime.yaml` is the
editable declaration. Its `delivery.re8ch.com/reconciler: native-flux` annotation
keeps it out of the SSA partition. The existing domain render includes it in
`clusters/re8ch/domains/main-legacy/data-storage`, owned by
`flux-system/reconcile-main-legacy-data-storage`. The shard also owns retained
configuration/secrets; it now explicitly checks the Deployment's rollout.

A signed main merge is sufficient: the existing verified GitRepository (30s
interval plus existing GitHub Receiver) triggers this native Kustomization.
Its events use the existing GitHub Provider/Alert. No workflow_dispatch, release
bundle, cosign packaging or promotion is required for this unit. Other SSA
units keep their existing execution path until individually migrated.

Before cutover, confirm no main-domain-release execution is active. After the
merged root removes the SSA declaration, delete only the old RoleBinding and
Role named `main-data-storage-supabase-pg-re8ch` in `supabase-pg-re8ch` through
the audited platform path (server dry-run first). Root prune=false does not
remove these automatically. Retain the historical operation-state ConfigMap
as audit evidence; it is not a writer. Never re-enable the old contract while
native reconciliation is active.

Acceptance: exact signed source/applied revision, native inventory containing
Deployment/supabase-pg-re8ch/supabase-rest, Ready deployment and a functional
HTTP response. Check GitHub commit status separately from runtime health.
This ownership-only cutover deliberately does not restart a healthy Pod.
Rollback is a signed desired-state revert; moving back to SSA requires first
stopping the native writer and reviewing the old contract and permissions.
