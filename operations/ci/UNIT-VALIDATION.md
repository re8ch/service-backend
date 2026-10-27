# Unit validation rollout

`validation_units` in the domain catalog drives selection of the first 15 test
files. Only `test_incremental_ci` stays in always-tests; the other former baseline
files are selected when their inputs change on PRs. Selected PR units may consume
trusted main success evidence; unaffected units are never added for cache checks.
On main, `candidate` enables
exact success-evidence matching after closure checks; `reuse_blocked` documents
known reasons to execute instead. Undeclared
files retain legacy domain selection and have reuse policy `never`.

Each declaration separates source, data and helper patterns, and required tools.
The test file and common helpers/environment are included automatically. Directory
patterns include additions and deletions. Render consumers additionally declare
Kustomize `roots`: selection follows local references in both base and head, even
when the changed input is outside the root directory. Evidence keys include those closures, not just the root-directory pattern
returned by `inputs_for`. Some scan scopes deliberately remain broad; declarations alone are
not successful validation evidence.

Consumer input patterns do not automatically own unknown files. Only exact
declared paths or the existing reviewed domain/focused patterns establish ownership.
Unknown source/manifests/helpers, deleted unregistered tests, common test environment,
selection policy and fixtures retain full fallback. Changes to the Kustomize helper
select its three Flux consumers and its own regression tests; native YAML consumers
also select the parser contract tests. Codename history alone retains full fallback;
history accompanying a mapped release lock stays incremental.

The CI CLI reads historical Kustomize definitions at the diff's merge base with
`git ls-tree` and `git cat-file --batch`; it does not check out or execute historical
code. Removed and new reference edges are unioned. Unavailable historical objects,
historical symlinks or unreadable dependencies cause full validation with a reason.
Explicit `--path` simulations use the current tree; real PR comparisons use `--base`.

GitHub supplies job execution and artifact storage. It does not infer this
repository's YAML/data dependencies or per-file pytest phase timings. The bounded
catalog adapter and opt-in pytest observer fill that gap; neither is a scheduler.

## Current artifacts

- `unit-selection/selection.json`: the impact plan and a reason for
  every test file, including files not selected and docs-only runs without unit.
- `unit-observation/units.json`: actual node IDs, setup/call/teardown outcomes and
  durations, collection failures, process exit code, Python/packages/tool versions
  and an allowlisted GitHub/runner identity. It is an observation, not cache evidence.
- `unit-observation/junit.xml`: pytest's native JUnit output.

Actions summaries show selection and execution separately. `planned` is not a
pass; `executed` can include skips or xfails, which are counted separately. A failed
setup/call/teardown is failed; unstarted or interrupted selected work is blocked.
Unselected files are unaffected by the current selection rules. Reuse is disabled.
Failure observations are uploaded with `always()` and retained for 14 days.

Run locally with:

```sh
python -m pytest -q -p operations.ci.unit_report \
  --unit-report-dir=/tmp/unit-observation \
  --junitxml=/tmp/unit-observation/junit.xml
```

## Ordered follow-ups

1. Share immutable input snapshots without changing consumer assertions.
2. On each main merge, publish exact-input successful unit evidence; daily full
   regression always executes and refreshes evidence.
3. Let PRs consume trusted main evidence with exact keys and current case matching.

Each follow-up starts from main after its predecessor merges. Compare identical
test sets with alternating runs, record cold/hit/invalidated cases when reuse
exists, and do not equate profiling overhead or runner noise with saved CI time.

## Main success baseline (PR4)

`main-unit-baseline.yml` runs after each main push. It executes impacted legacy
files, always executes `test_incremental_ci`, and checks all 14 declared candidates
against the latest trusted baseline. Eleven currently have closed inputs;
infrastructure contracts, domain publication and live domain sources explicitly
remain real execution because Git/external environment/topology inputs are not
closed. PR workflows consume success evidence through `unit_pr`, but never publish it.

`unit_baseline` collects the current case set without running fixtures, then
matches it and the exact input fingerprint. Fingerprints include file contents,
directory members, gitlinks and checkout state, declarations, shared code,
execution configuration and actual dependency/tool versions. Unsafe/missing
inputs, symlinks and unsupported Kustomize closure force execution. Full impact
(including unknown risks) and daily regression bypass result reuse completely.
The existing session input snapshots still avoid repeated parsing during real
execution and are never persisted.

The API adapter reads only the latest completed main run for each approved event
(main baseline push; full regression schedule/manual dispatch), selects the newest,
and downloads at most one artifact. It checks GitHub-provided repository,
workflow, branch, event, final conclusion and run attempt, plus archive digest,
shape, size, schema and publisher identity. A newer failed run blocks inheritance;
missing, damaged, expired or incompatible evidence falls back to execution rather
than searching older artifacts. Only approved main workflows with clean matching
checkouts may publish.

The `unit-success-evidence` artifact contains one `evidence.json`: at most 14
records / 2 MiB. Each record stores fingerprint, case IDs, complete-pass result,
duration, original verification time/SHA/run/attempt and direct predecessor only.
No parsed inputs, credentials, business data or unbounded history are retained.
All artifacts expire after 14 days through GitHub retention. Inheritance preserves
the original verification time; evidence older than 14 days cannot be reused.
Daily full runs refresh real evidence. Skip/xfail/xpass/incomplete/failed cases do
not certify a unit; failure prevents publishing the current snapshot.

For main baseline, full-main and PR consumer runs, `unit-observation/units.json` is the aggregate
execution/reuse/unaffected/blocked decision record. Reused records link the original
run and expose SHA, fingerprint, case count and age. Actual pytest observations
and JUnit are under `execution/`; no synthetic executed cases are created for
reused units. `lookup_seconds` measures GitHub lookup/download; aggregate
`wall_seconds` includes lookup, collection, fingerprinting, execution and reporting.
Other full regressions retain the ordinary observation layout above.

GitHub supplies artifact storage, digests, retention and workflow identity. It does
not infer this repository's validation dependencies or pytest case equivalence;
these bounded adapters fill that gap without a scheduler, new credential store or
artifact-deletion permissions. Shared declarations remain the input authority.

## PR consumption (PR5)

The existing `ci.yml` unit job calls `unit_pr` with the original impact plan.
It keeps the same required-check name and aggregator. Job permissions are only
contents/read and actions/read; fork/token restrictions fall back to real selected
tests. There is no pull_request_target workflow or success-evidence upload from PRs.
The main publication CLI retains its approved-main workflow and clean-SHA guards.

Main expands selection to refresh all candidates; PR consumption never does.
Docs-only plans start neither pytest nor an artifact lookup. Full/unknown-risk
plans and selections without reusable candidates skip network lookup. Selector
tests must remain selected and always execute. Collection, input and case-set
matching use the same adapters as main. Missing/incomplete execution cannot become
a successful current check, and reused units get no synthetic JUnit test cases.

The aggregate reports `consumer` identity for PR runs, per-unit origin evidence
and reasons, and lookup/collection/fingerprint/execution/post-validation timings.
No `evidence.json` is produced in consume-only mode, including forced regression.
Shared fixtures in the evidence tests have an explicit focused consumer mapping.

Changing adapters, declarations or workflow configuration invalidates older
fingerprints; PR5's own infrastructure diff intentionally forces full execution.
The first main baseline after merge refreshes these keys. Never weaken matching
or manufacture trusted evidence merely to demonstrate a warm run.

Measure actual selected scopes: docs, local tools, local manifests and shared
inputs. Cold/hit/partial local experiments with injected baselines exclude GitHub
network cost. A hit is only possible when selected inputs already have identical
main evidence; a newly changed dependency normally invalidates its consumers.
Narrow impact already removes unrelated units, so cheap selected tests may save
less execution time than lookup and fingerprinting cost. Do not present local
hit savings as end-to-end CI acceleration; retain actual runner identity and total
wall time, including lookup, in reports.
