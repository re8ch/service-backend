# Unit validation rollout

This first slice records existing execution. It does not narrow impact, enable
reuse, skip tests, or change assertions. `validation_units` in the domain catalog
describes the first 15 test files; `candidate` means eligible for a future input
closure review, not permission to skip a test today. Undeclared files retain
legacy domain selection and have reuse policy `never`.

Each declaration separates source, data and helper patterns, and required tools.
The test file and common helpers/environment are included automatically. Directory
patterns must include additions and deletions. Some scopes deliberately remain
broad until transitive consumers can be proved; these declarations alone must not
be treated as successful validation evidence.

GitHub supplies job execution and artifact storage. It does not infer this
repository's YAML/data dependencies or per-file pytest phase timings. The bounded
catalog adapter and opt-in pytest observer fill that gap; neither is a scheduler.

## Current artifacts

- `unit-selection/selection.json`: the unchanged impact plan and a reason for
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

1. Narrow impact and always-tests using the reviewed declarations, including the
   union of base/head dependencies; retain unknown-input full fallback.
2. Share immutable input snapshots without changing consumer assertions.
3. On each main merge, publish exact-input successful unit evidence; daily full
   regression always executes and refreshes evidence.
4. Let PRs consume trusted main evidence with exact keys and current case matching.

Each follow-up starts from main after its predecessor merges. Compare identical
test sets with alternating runs, record cold/hit/invalidated cases when reuse
exists, and do not equate profiling overhead or runner noise with saved CI time.
