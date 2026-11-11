# GitHub-native agent and provider publisher runbook

Use this runbook when an independently released MCP gateway, provider, or
platform agent must move from a reviewed source change to a verified runtime
without giving a general CI runner cluster credentials. It records the delivery
path exercised by the ConnecTool catalog-gateway and PostgreSQL provider
recovery in November 2026.

The outcome is not “a PR merged”. Completion requires one immutable artifact,
one reviewed desired-state pointer, a successful rollout, and a real tool call
through the public plugin boundary.

## Authority and identity boundaries

| Responsibility | Preferred identity | Allowed authority |
| --- | --- | --- |
| Source review and checks | GitHub App or human pull request author | repository contents and checks only |
| Linux tests and artifact build | GitHub-hosted Enterprise runner | checkout, packages, attestations; no kubeconfig |
| Repository governance | dedicated Governance GitHub App | Rulesets, Variables, Environments and organization settings |
| Desired-state promotion | signed pull request in the owning GitOps repository | one digest or release pointer |
| Runtime apply | approved platform publisher/executor | named objects in the declared release domain |
| Runtime observation | plugin or read-only platform observer | readiness, image IDs, events and sanitized logs; never Secret values |

Observer, Maintenance and Executor responsibilities remain separate. A GitHub
App installation token is short-lived and repository-scoped. Do not put App
private keys, installation tokens, runner tokens, kubeconfigs or decrypted
Secret material in Git, workflow arguments, logs, MEMORY, or artifacts.

GitHub-hosted runners are the default. Use Macmini only for work that requires
`/Volumes/A1`; use R640 only as the declared cluster mirror or controlled
operations fallback. A self-hosted runner is not a shortcut around network,
Ruleset, Environment, or credential policy.

## Delivery sequence

### 1. Establish a clean source revision

1. Run `python operations/workspace-controller/workspace.py doctor`.
2. Read the owning repository instructions and module MEMORY.
3. Create one task worktree from the current GitHub default branch.
4. Keep implementation, release versioning, and desired-state promotion as
   separate reviewable concerns when they have different rollback units.
5. Sign commits, push a branch, open a pull request, and wait for every required
   check. Do not merge around a Ruleset or grant bypass to unblock delivery.

### 2. Make CI independent of the cluster

The source workflow must run on a GitHub-hosted Enterprise runner and perform
all deterministic work before runtime promotion:

- unit, contract, schema, render and policy tests;
- a container import/startup smoke test that imports every packaged module;
- Helm/Kustomize rendering where applicable;
- secret and dependency review;
- one `linux/amd64` production image build for R640 unless another platform is
  explicitly required;
- SBOM, provenance attestation and keyless signature publication.

Build once per source revision and build configuration. Do not rebuild the same
source merely to move it between stages. A release tag may trigger publication,
but the runtime declaration must pin the resulting manifest digest, never the
tag.

If a job is queued or cannot reach a dependency, diagnose the gate in this
order:

1. GitHub Actions/Enterprise runner availability and repository Actions policy;
2. required Environment approval and Ruleset status;
3. job permissions for `contents`, `packages`, `id-token`, attestations and
   checks;
4. public DNS/TLS and registry reachability from the hosted runner;
5. registry repository/project name and package write scope;
6. image architecture and pinned base-image availability.

Do not route a hosted build through the cluster, add an ad-hoc host route, or
move it to a privileged self-hosted runner merely because an outbound request
failed.

#### R640 runner and Mihomo egress

An explicitly declared R640 job uses the reviewed node-local Mihomo consumer;
it does not inherit authority to change host routing. The runner service uses
`http://127.0.0.1:17891` for GitHub, codeload, release assets and public registry
traffic. Keep GitHub asset domains on this path: direct connectivity is
intermittent, while Mihomo's `GITHUB` policy selects the region-pinned, measured
global exit. Private, cluster and management CIDRs remain direct through the
checked-in runner `NO_PROXY`; never repair a job by adding a host route or by
changing RouterOS, Cilium, Gateway API or the R640 public authority.

Proxy configuration is part of the locked runner installation, not a workflow
credential. Each runner directory receives the reviewed non-secret `.env`; the
locked `github-runner` account has no sudo, kubeconfig, cluster token or rootful
Docker access. Node-based Actions that must honor the inherited proxy set
`NODE_USE_ENV_PROXY=1`, including post-job cleanup such as GitHub App token
revocation. A workflow must not print, replace or persist the runner proxy
environment.

Container builds use the shared named builder `r640-mihomos`. Its BuildKit
daemon uses host networking plus explicit upper- and lower-case HTTP(S) proxy
variables, so its `127.0.0.1:17891` resolves to the host listener. Dockerfile
`RUN` sandboxes cannot use host loopback; the installed Docker proxy contract
provides the R640 node service identity `10.250.0.128:17891` instead. Workflows
must select or inspect `r640-mihomos` explicitly; an implicit `default` bridge
builder can silently bypass the governed path.

Release authentication remains job-scoped even though the builder is shared.
Create `DOCKER_CONFIG` under `RUNNER_TEMP`, write only the short-lived registry
authorization required by that job, and remove it in an `always()` cleanup.
Keep the shared Buildx index separate from this disposable auth directory. For
Harbor builds, `BUILDKIT_NO_CLIENT_TOKEN=1` keeps OAuth token exchange inside
the region-pinned builder rather than moving it to an unreliable client-side
path.

Before blaming GitHub or increasing a timeout, verify all three layers:

```bash
curl --fail --proxy http://127.0.0.1:17891 https://api.github.com/rate_limit
sudo -u github-runner docker run --rm docker.io/library/alpine:3.22 /bin/true
sudo -u github-runner env \
  XDG_RUNTIME_DIR=/run/user/1002 \
  DOCKER_HOST=unix:///run/user/1002/podman/podman.sock \
  BUILDX_BUILDER=r640-mihomos \
  docker buildx inspect r640-mihomos
```

The first proves Mihomo's public path, the second proves the rootless container
transport, and the third proves the governed BuildKit daemon. Recreating the
builder discards its cache and is allowed only while every R640 runner slot is
idle. The authoritative installation and repair procedure remains
[`operations/ci/runners/README.md`](../../ci/runners/README.md); this runbook
records only the publisher-facing contract.

### 3. Promote the immutable artifact

After the source PR is merged and its release workflow succeeds:

1. Read the manifest digest and attestation result from the completed Actions
   run.
2. Create a fresh worktree in the owning desired-state repository.
3. Change only the immutable image/release pointer and its deterministic release
   payload.
4. Update the authority-transfer source digest when that repository requires
   it.
5. Run the repository's full validation suite and deterministic compiler check.
6. Open and merge a second PR after required checks succeed.

For the agent runtime domain, the exercised validation is:

```bash
python operations/releases/compile_domain.py
python -m pytest -q
python operations/releases/compile_domain.py --check
git diff --check
```

### 4. Apply only reviewed objects

Use the approved platform publisher first. If that path is unavailable and the
operator has explicitly authorized the R640 fallback, apply only objects
extracted from the merged release payload:

1. server-side dry-run with the declared field manager;
2. inspect every field-ownership conflict;
3. use `--force-conflicts` only for objects listed by the reviewed authority
   transfer;
4. apply the same bytes with the same field manager;
5. wait for the named Deployment rollout and inspect readiness, immutable image
   ID and restart count.

Do not replay the full release bundle to change one Deployment. Do not use
`kubectl exec`, read Secret values, or treat an emergency runtime write as
permission to bypass the source and desired-state pull requests. Migrated
high-cadence agents may use this bounded publisher path; unrelated domains keep
their declared reconciler and ownership model.

### 5. Verify the public capability

Runtime readiness alone is insufficient. Verify, in order:

1. new Pod is Ready with the reviewed image digest and zero unexpected restarts;
2. gateway/provider health endpoint succeeds;
3. provider catalog registration succeeds without returning credential
   material;
4. authenticated organization context and access status resolve correctly;
5. `tools/list` contains core tools even when dynamic discovery is unavailable;
6. the newly published provider tool appears after the bounded catalog refresh;
7. a read-only catalog/status call succeeds through the Re8ch plugin;
8. a plan call succeeds before any create/update operation;
9. bindings contain only opaque references and usage events remain tenant-scoped.

Restore the user's original organization context after cross-tenant acceptance.
Do not create or delete a canary resource unless that lifecycle mutation is
explicitly authorized.

## Failure isolation patterns

The following failures were found during the PostgreSQL provider rollout and
should be checked before changing architecture:

| Symptom | Check | Corrective pattern |
| --- | --- | --- |
| Container starts locally but crashes in runtime | packaged Python/Go modules and image smoke test | import/start the built image in CI |
| Provider receives HTTP 405 | public service port versus admin listener | publish the exact internal admin port and NetworkPolicy edge |
| Provider receives HTTP 401 | handler credential name and Secret reference | bind the existing opaque admin credential; never copy its value |
| Connection times out | namespace/pod selector and target port | add the smallest reviewed NetworkPolicy rule |
| Every plugin tool becomes unavailable | gateway fetches dynamic catalog before MCP initialization | keep initialization and core tools independent of dynamic discovery |
| Core tools work but provider tool is absent | catalog revision/session refresh | inspect gateway catalog logs and refresh `tools/list`; do not redeploy tenant-core blindly |
| Valid service plan returns `INVALID_ARGUMENT` | controller validation and connector error projection | diagnose as a separate lifecycle-contract issue; do not create the resource |

The catalog gateway must fail open only for the existing tenant-core directory:
catalog discovery failure yields an empty dynamic catalog plus a diagnostic log.
It must continue to fail closed for provider routing and tenant authorization.

## Rollback

Rollback is another reviewed digest promotion:

1. identify the last Ready immutable digest and its successful release evidence;
2. open a desired-state PR restoring only that digest;
3. run the same deterministic checks;
4. merge and apply the same named object with the declared field manager;
5. verify core tools, organization context and the affected provider separately.

Do not rebuild the old source, delete provider state, rotate credentials, or
roll back unrelated providers. A catalog/provider regression must not require a
tenant-api or shared gateway rollback when routing can be reverted independently.

## Exercised evidence

- ConnecTool failure isolation: `among-clusters/connectool#32`
- ConnecTool v0.6.10 release: `among-clusters/connectool#33`
- Catalog-gateway digest promotion: `8iops/agent-gitops#151`
- PostgreSQL provider source and packaging: `re8ch/service-backend#2785`,
  `#2789`, `#2790`
- PostgreSQL provider runtime promotions: `8iops/agent-gitops#143` through
  `#150`

These references are evidence of the exercised path, not permanent substitutes
for current Actions checks, runtime health, or repository Rulesets.
