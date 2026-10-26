# Unit validation rollout

`validation_units` in the domain catalog drives selection of the first 15 test
files. Only `test_incremental_ci` stays in always-tests; the other former baseline
files execute when their inputs change. `candidate` means eligible for a future
input closure review, not permission to reuse a previous success. Undeclared
files retain legacy domain selection and have reuse policy `never`.

Each declaration separates source, data and helper patterns, and required tools.
The test file and common helpers/environment are included automatically. Directory
patterns include additions and deletions. Render consumers additionally declare
Kustomize `roots`: selection follows local references in both base and head, even
when the changed input is outside the root directory. Future evidence keys must
include those closures, not just the root-directory pattern returned by
`inputs_for`. Some scan scopes deliberately remain broad; declarations alone are
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
