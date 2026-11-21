# PublicEdge dispatcher Worker

A Cloudflare Worker Cron Trigger that drives the PublicEdge authority check.
GitHub `schedule` drops most high-frequency cron runs in this repository, so
this Worker supplies the cadence.

Every 15 minutes it does two things:

1. **Dispatch.** It calls `workflow_dispatch` on
   `.github/workflows/public-edge-authority.yml` at `main`.
2. **Staleness check.** It looks for a *conclusive* run updated within
   `STALE_AFTER_MINUTES` (default 60). A run is conclusive when it completed
   and at least one vantage job produced a verdict, meaning it failed on
   authority or succeeded without a `PublicEdge inconclusive` annotation.
   - No conclusive run: it opens a single issue titled
     `[PublicEdge] authority observation stale`, unless one is already open.
   - A conclusive run again: it comments on that issue with the run link and
     closes it.

The GitHub `schedule` stays as an hourly fallback.

## Deploy (manual, by an operator)

```sh
cd platform/public-edge-manager/dispatcher-worker
npx wrangler secret put GITHUB_TOKEN
npx wrangler deploy
```

`GITHUB_TOKEN` is a fine-grained token scoped to `re8ch/service-backend`
only, with these permissions: Actions read/write, Issues read/write, and
Metadata read. Keep it out of Git. The Worker has no HTTP route
(`workers_dev = false`).

## Test

```sh
node --test platform/public-edge-manager/dispatcher-worker/index.test.mjs
```
