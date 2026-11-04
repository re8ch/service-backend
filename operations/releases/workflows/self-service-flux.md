# Self-service Flux delivery and observability recovery

Use for a user-authorized service rollout, repair or deployment-status request.
This workflow adds no permissions. Start with tenant_self_service_capabilities
and inspect the current Re8ch model-callable tools and schemas. Resolve the
authenticated organization and active membership; Re8ch Admin infrastructure
authority is not Re8ch tenant application membership. Never use local
credentials to bypass the plugin.
Apply the Flux steps below only when the current authority ledger and live
reconciler confirm Flux still owns the target. For a cloud SSA owned target,
follow its active delivery contract instead of annotating a retired Flux source.

Resolve the active repository, controller namespace, source and reconciler
from service-backend's operations/flux-federation/authority.yaml and live Flux
objects. For observability the current owner is
https://github.com/siren-observer/observability-gitops, root clusters/re8ch,
GitRepository/domain and Kustomization/domain in flux-observability-system.
Verify that mapping on each run. Frozen service-backend observability copies
and suspended legacy OCI releases are rollback evidence, not publishing paths.

For platform administrators, use an explicitly exposed Re8ch operation whose
schema accepts `argv` only if it is present in the current tool list. Inspect
only the named Flux objects and affected workloads. Use an explicitly exposed
observability status operation for fixed health probes when available. A
backend catalog, authorization flag, or this workflow does not make an absent
tool callable. If the required operation is unpublished, report that precise
gap and do not claim a runtime check. Do not read Secret values or return
unbounded logs. Ordinary tenants without those tools must use their authorized
service APIs and a reviewed GitHub proposal; they must not fall back to an
administrator identity or shell. A grant is not readiness.

Prepare the smallest static change in the active repository's dedicated
worktree. Read its AGENTS.md. Pin new images by immutable digest, reuse existing
artifacts, and preserve Secret references. For a new service include its
namespace, quota, bounded runtime identity, probes and deletion policy in the
reviewed declaration. Reuse existing Flux sources/controllers. Do not add a
Python planner, duplicate compilation, bundle or promotion stage for a YAML
change. Run relevant validation, sign the commit and open a PR. Inspect checks,
reviews, Rulesets and the installed App's scope. Merge only within the user's
authorization and repository rules; never use ruleset bypass. Record the exact
merged commit and verify its signature.

For an already authorized post-merge platform rollout, an exposed tool with
the documented `argv` schema can request the standard Flux reconciliation
annotation. First preview with that tool (replace the token with a unique
operation identifier):

    ["annotate","gitrepository.source.toolkit.fluxcd.io/domain","-n","flux-observability-system","reconcile.fluxcd.io/requestedAt=REQUEST_ID","--overwrite","--dry-run=server","-o","name"]

After a successful preview, repeat the same bounded command without
--dry-run=server. This changes only metadata, not desired state. Read the source
until status.lastHandledReconcileAt matches and status.artifact.revision equals
the merged commit with SourceVerified=True. A source revision event normally
triggers its Kustomization automatically. If necessary, preview and request the
same annotation on kustomization.kustomize.toolkit.fluxcd.io/domain. Never
unsuspend a legacy owner or patch status to force a green result. Use bounded
checks during the active request; no new background polling service.

Check Kustomization status.lastAppliedRevision and observedGeneration as well
as Ready. Inspect actual Deployment/StatefulSet/operator readiness and the
affected health endpoint. For OpenCost, verify VMAgent/opencost schedules,
its collector sends metrics, Deployment/opencost becomes available, and the
OpenCost API health probe succeeds. Root wait:false can be green while a
dependent service remains broken. Existing authorized Flux CLI users can run:

    flux get all -n flux-observability-system
    flux tree kustomization domain -n flux-observability-system

Those commands show native objects and inventory; no compatibility projection
is required for this domain. Do not claim the 17 main-domain SSA units have the
same coverage. Report PR, merged revision, plugin audit reference, reconciliation
result and workload/service health separately. On failure fix the active source
or revert through a signed PR; do not restart unrelated controllers or delete
PVCs as a shortcut.

Implementation plan and limits:
operations/releases/SELF-SERVICE-FLUX.md in re8ch/service-backend. In particular,
publishing this workflow does not implement general tenant Flux endpoints or
grant permission to reconcile arbitrary namespaces.
