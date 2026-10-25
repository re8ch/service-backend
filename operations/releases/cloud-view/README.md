# Flux public runtime evidence

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
