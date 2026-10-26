# Flux public runtime evidence

## Publication withdrawal

Detailed public inventory publication is withdrawn while the restricted
configuration and aggregate-only public status contracts are implemented.
The existing main observer workflow now replaces the public snapshot with an
`unavailable` envelope and publishes a neutral Check with no resource payload.
The public viewer must show Unknown; this is not a cluster health result.
The job no longer obtains a Kubernetes identity or reads cluster resources.
Use authenticated Headlamp / platform APIs for detailed inspection.

This does not erase historical Checks, Git history, caches or third-party
copies. History cleanup follows configuration migration and acceptance.
Containment is verified only after the main workflow succeeds and an anonymous
GET of `latest.json` contains `unavailable: true` with no resource inventory.

The description below records the prior publication architecture; its detailed
inventory publisher is no longer active after this change is deployed.

The viewer is independently released at https://github.com/re8ch/headlamp-flux-static-github-ops .
https://re8ch.github.io/service-backend/ redirects existing Headlamp links to
https://flux-status.re8ch.com/ . No browser GitHub API requests remain.

This directory retains the bounded observer and its projection tests. The existing
Executor App publishes Checks and reads release metadata with a short-lived token.
The exact main observer workflow uses Actions OIDC to write the public R2 snapshot;
no cloud write secret is stored in the cluster or repository. Specs, credentials and
raw condition messages are excluded by the cloud ingestion whitelist.

The viewer refreshes every 15 seconds. The original observation expires after 15
minutes regardless of reads. Failed collection attempts publish an unavailable marker;
failed upload leaves the last observation to expire normally. No controller is added.
The five-minute GitHub cron is offset from minute zero and isolated from the daily
node audit concurrency group. GitHub schedule delays remain possible and must be
measured in production; manual success is not proof of reliable periodic collection.

Native Flux remains the sole Headlamp resource writer. Roll back the signed plugin
version/digest and cloud source revision through PRs, never reactivate retired SSA.
