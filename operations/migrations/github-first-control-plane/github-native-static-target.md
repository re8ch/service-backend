# GitHub-native static control plane

The target is to decompose Flux's decisions into immutable inputs, pure
derivations and GitHub-managed admission. GitHub Apps own integration identity
and event routing. Actions supply bounded compilation/test capacity. Production
execution consumes an approved operation and a signed digest; it does not
resolve source, render charts, choose versions or invent remediation.

This is the requested destination, not a claim of production activation. The
current nine-domain migration and its retirement gates remain in force. Do not
build another general-purpose Flux controller to implement this destination.

## Responsibility assignment

The machine-readable source is
[`static-control-plane.yaml`](../../releases/static-control-plane.yaml).
Generate a domain-specific target with:

```sh
python operations/releases/static_domain_standard.py target --domain agent-runtime
```

The output is a planning document, not a runtime operation or permission grant.
It retains the current execution identity separately from the target, so an
Actions OIDC credential cannot silently be replaced by a GitHub App token.

| Existing responsibility | Derive ahead of execution | GitHub or runtime service |
| --- | --- | --- |
| Source reconciliation | exact repository ID, commit SHA, source/dependency digests | Git, Packages and verified producer events |
| Helm/Kustomize rendering | canonical objects, scope, inventory, deterministic bundle | bounded build job; no renderer in executor |
| Dependency ordering | acyclic waves and explicit health predicates | executor checks fresh dependency evidence before each wave |
| Identity/RBAC | exact subjects, targets, verbs, namespaces | workload authentication and Kubernetes RBAC enforce it |
| Change admission | policy declarations and App-produced check results | organization Rulesets, PRs and configured review policy |
| Image updates | permitted digest delta | promoter App proposes PR; Rulesets decide merge eligibility |
| Reconciliation decision | pure function of desired revision plus authenticated observations | App adapter supplies snapshots, durable dedupe and dispatch |
| Prune | previous accepted inventory minus proposed inventory | executor verifies live UID/ownership before deletion |
| Rollback | previous approved digest and declared rollback policy | executor verifies current preconditions; stateful recovery remains explicit |
| Health/drift | predicates, allowed field ownership and response policy | small read-only observer produces fresh facts |
| Reporting | result-to-status/issue mapping and dedupe key | Checks, Deployments, Issues and Projects |

Helm charts that use `lookup`, random generation, mutable downloads, hooks or
stateful migrations need explicit classification. A pinned renderer alone does
not make these pure. Secret values stay outside Git and bundles; only references
and credential contracts are compiled. Unknown cases block conversion.

## A concrete change

For a ToolHive version change, a producer publishes an immutable chart/image.
The promoter validates its provenance and proposes the exact digest update.
Rulesets enforce signatures and the existing required checks/review policy.
The compiler emits canonical objects, an inventory, apply waves, health
predicates, permitted deletions and a rollback digest. Each result is bound to
the source commit and contract digest.

After merge, the App adapter re-reads protected main and the approved catalog;
the webhook is a notification, not authorization. The executor App records an
operation bound to that commit, bundle, target, sequence and approval. A small
executor obtains its separate workload identity, verifies the operation,
performs server dry-run and bounded SSA, and persists the accepted result.
It stops on stale sequence, changed ownership or failed preconditions.

The observer reports actual readiness and drift. The pure policy evaluator
can propose reapplying the same approved digest when the catalog permits it;
the executor must recheck authorization, freshness and serialization. This
requires runtime observation, but does not require source/render/policy logic
to run continuously inside Kubernetes.

## GitHub services and exact boundaries

Custom Properties (`system`, `domain`, `service-tier`, `risk`,
`execution-profile`) select organization rulesets and route events. Restrict
who can edit authoritative properties, compare them with the reviewed catalog,
and use numeric repository/installation IDs when authorizing execution. A
property edit must never grant a Kubernetes capability by itself. Keep a
baseline ruleset covering unclassified repositories so missing properties
cannot remove protection.

Rulesets enforce admission on GitHub servers. Required checks must be bound
to the intended installed App where supported. Preserve existing required
checks until replacement publishers pass canaries. Merge queue adoption also
requires check production for merge-group SHAs; do not enable it before that
path exists. The current single-maintainer review configuration must not be
replaced by an impossible two-person approval requirement.

GitHub Deployments and Checks are evidence surfaces. Creating a Deployment is
not proof of authorization. An external executor must verify explicit approval
bound to the exact operation. Actions Environment protections are not assumed
to gate arbitrary external App API calls.

Webhooks carry events to a minimal adapter. Verify HMAC over raw body bytes,
validate the installation/repository association, persist before acknowledging,
deduplicate delivery IDs, and make processing idempotent across reordered and
duplicate events. Provide bounded retries, dead-letter handling and missed-event
recovery. GitHub webhook delivery is not treated as an exactly-once queue.

Codespaces supplies a reproducible editor and offline plan preview from the
same locks. It does not hold a production executor key or host the production
event consumer. Actions remains useful for builds/tests/signing; a workflow
per observation, policy decision or status update is not the target.

## Reuse and outstanding work

Reuse `unified_bundle.py`, `ssa_executor.py`, accepted inventory and the
domain contracts. Refactor their operation provenance through a separate
reviewed change: current operations require workflow/run/OIDC fields, so App
registration alone does not create an App execution path. A GitHub installation
token authorizes GitHub APIs, not Kubernetes.

Keep Observer read-only on GitHub if that is its installed permission boundary.
It can submit authenticated facts to a writer; Executor publishes execution
Checks/Deployment statuses and Maintenance publishes Issues/Project updates.
Do not claim that a read-only App can create Checks.

Observed on 2026-10-25 in the authenticated GitHub settings UI:
`re8ch-gitops-executor` has App ID `5426288`, webhook Active unchecked and an
empty webhook URL. Thus event intake remains an activation dependency. This
observation does not attest effective installation permissions or a deployed
runtime. Repository declarations likewise do not prove running Pod health.

Activation proceeds through the gates in the machine-readable target: verify
App installations and policy coverage; prove intake and App-bound execution;
complete one domain canary with replay, drift recovery and rollback; then
retire old writers only after the established stability and zero-consumer gates.

References: [organization property targeting](https://docs.github.com/en/organizations/managing-organization-settings/managing-rulesets-for-repositories-in-your-organization/creating-rulesets-for-repositories-in-your-organization),
[available rules](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets),
[webhook best practices](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks).
