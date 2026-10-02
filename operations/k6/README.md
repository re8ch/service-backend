# Active functional checks

This is the deployable, low-rate functional-check template derived from the
`observable/` research in `RE8CHProjects/thiking-machine/teleops-fog-simulator`.
That prototype separates continuous probes, journeys, and bounded k6 load
experiments. This repository uses only bounded k6 functional checks: one virtual
user, three iterations, exact response assertions, a 15-second request timeout,
and no write operations. Stress tests remain one-shot work on a dedicated
load-generator cluster.

## Time scales and coordination

| Signal | Runner and vantage | Period | Purpose |
| --- | --- | --- | --- |
| PublicEdgeManager route health | edge controller | 15 seconds | Select a healthy backend; not a functional contract |
| Fast public contract | GitHub-hosted runner, outside K3S | 5 minutes at `2/5` | Catch public entry and authentication drift |
| Fast internal contract | `synthetic-monitoring` CronJob | 5 minutes at `4/5` | Catch service-DNS health drift |
| Public DNS authority | existing GitHub workflow | 15 minutes | Detect delegation and public authority drift |
| Standard public contract | GitHub-hosted runner | 30 minutes at `18,48` | Check discovery and service semantics |
| Standard internal contract | `synthetic-monitoring` CronJob | 30 minutes at `20,50` | Check less urgent internal service behavior |
| Change gate | control-plane PR CI | every PR | Validate contracts, tests and script loading without contacting production |

The offsets spread expected traffic. GitHub scheduled workflows are best effort,
so the phases are coordination hints, not a strict distributed clock. Each
internal CronJob forbids overlap, has a three-minute deadline, and retains failed
Jobs for inspection. A failed external k6 threshold fails the workflow. A failed
internal Job is visible in Kubernetes Job status and logs; alert routing can
consume that status through cluster telemetry.

## Where to see results

- **Public checks:** GitHub Actions → `k6 functional contracts` shows each run.
  The run summary lists every contract, pass/fail count, and HTTP p95. Download
  `k6-contract-summary` for the full JSON result (retained for 30 days). PR
  checks appear in `control-plane-ci` as `k6-contracts`, but validate contracts
  and load the script offline. Live production failures remain failures in this
  separate scheduled/manual workflow and do not block repair PRs.
- **Internal checks:** Headlamp's standard CronJobs, Jobs, and Pod logs in the
  `synthetic-monitoring` namespace show execution and the k6 threshold result.
  The existing `k6-contracts` CronJob is the standard cadence; the fast cadence
  is `k6-contracts-fast`.
- **Grafana:** these CronJobs do not currently remote-write k6 metrics or
  provision a k6 dashboard. Grafana is the right place for aggregate trends
  after the result exporter and alert rule are added; it is not the current
  source of individual run results.

## Reusable dataset

`clusters/re8ch/components/synthetic-monitoring/contracts.json` is the
reviewed dataset. Each probe declares an owner, `external` or `internal`
vantage, `fast` or `standard` cadence, URL, exact status, and optional response
assertion. External checks require a semantic JSON or header assertion;
internal checks may use a dedicated health endpoint. Public checks must use
HTTPS and public DNS. Internal checks use Kubernetes service DNS.

The same `smoke.js` runner selects probes with `PROBE_VANTAGE` and
`PROBE_CADENCE`. It emits stable `probe`, `owner`, `vantage`, `cadence`, and
`load_class` tags. Requests carry the synthetic headers and W3C baggage keys
used by the teleops prototype. Run IDs stay in headers and logs, never metric
tags. The script, dataset, validator, and schedule declarations can be copied
into another sub-organization repository; the validator accepts `--catalog`,
`--revisions`, `--k8s`, and `--workflow` paths. Set `--external-only` if that
organization has no internal CronJobs.

`operations/k6/revisions.json` is an append-only revision chain for this
versioned dataset. Every change to probes or cadence definitions increments
`version`, appends the previous digest and new SHA-256 of the exact JSON bytes,
and gives a reason. CI rejects rewritten history or a changed catalog without a
new revision. The validator also compares cadence declarations with the live
GitHub and Kubernetes schedules, so changing a schedule requires a reviewed
dataset revision.

## Run and extend

```sh
python operations/k6/validate.py --base github/main
docker run --rm \
  -e PROBE_VANTAGE=external -e PROBE_CADENCE=all \
  -v "$PWD/clusters/re8ch/components/synthetic-monitoring:/work:ro" \
  -w /work grafana/k6:1.3.0 run smoke.js
```

Add a public domain only after checking its declared PublicEdge route, owner,
expected semantics, and current behavior. Do not declare an existing 5xx or
unreachable route as an accepted baseline. Internal checks must use read-only
endpoints. Authenticated or state-changing journeys require scoped synthetic
identities, cleanup steps, and separate review. Keep credentials and raw
response bodies out of Git and workflow artifacts.
