# API acceptance cases

`cases.json` specifies 55 finite collection GET cases through the authenticated
self-service `platform_tool_call` API. These are a first explicit suite, not full
plugin operation coverage. Each case fixes the backend, tool, operationId,
OpenAPI hash, bounded request and expected status. CLI exit zero alone is never
acceptance. No credentials are stored here.

## Execute and assess

1. Discover `platform_tool_catalog` using the existing RE8CH login. Confirm the
   exact tool/schema and backend availability; unavailable means not run.
2. Sequentially call `platform_tool_call` with each case's backend, toolName and
   arguments. Do not parallelize this bounded checker or silently retry errors.
3. Save each complete MCP envelope as
   `{"caseId": "...", "result": {"content": [...], "isError": false}}` in a JSON array
   outside Git. Retain error attempts separately when retrying; never replace an
   error with a pass. Capture run time, login context reference (no credentials),
   deployed image digest and catalog availability alongside the report.
4. Run `python operations/plugin-capabilities/api-acceptance/verify_report.py REPORT`.
   Exit 1 means failed, invalid or missing cases. All four Schemathesis checks,
   status 200, matching operation/schema/cluster and an audit reference are required.
   This validates report assertions, not authenticity of arbitrary supplied files.
5. GET cases create nothing and need no cleanup. Publish summaries and audit
   references only; keep raw runtime telemetry outside Git.

Local regression: `pytest -q tests/test_plugin_api_acceptance.py tests/test_plugin_http_conformance.py`.
The former verifies case-to-OpenAPI bindings and report failure behavior. The latter
uses real Schemathesis against controlled HTTP responses including negative cases.
Neither local test success proves live acceptance.

## Anatomy write lifecycle case (explicitly authorized live run only)

Preconditions: reviewed live controller hash matches Anatomy authority; caller is
an authorized administrator; existing canary namespace and a Sealed RuntimeCommit
are verified. Use a unique run-specific RuntimeDiff name and the same commit in
baseRef/targetRef. No Pod/PVC/snapshot capacity is requested by this operation.

Steps and assertions: server dry-run succeeds; create succeeds and record UID;
read until Ready within a bounded timeout and assert added/removed/modified empty;
repeat create and expect AlreadyExists without replacement; JSON Patch only an
acceptance annotation using UID/resourceVersion test preconditions; re-read and
assert annotation, UID and spec; delete with DeleteOptions UID/resourceVersion
preconditions; re-read and require NotFound. Always attempt cleanup in a finally
path; if identity/connectivity prevents it, report the exact remaining object and
UID. Never blindly retry an uncertain create or delete.

The live run on 2026-10-04 passed this lifecycle (create audit
op_dfb3d0cc9df6424ebab8d3706cac69f5; cleanup audit
op_cf8afda82e5a4c03a0b624a7a74bba33). It used native kubectl API wrapping;
HTTP status/header acceptance of mutations and dedicated self-service mutation
adapters remain incomplete. Updating a terminal request's spec is not a supported
reconciliation test. Stagehand UI semantics and other write operations need their
own fixture, expected effect and cleanup contracts.

## Authenticated host runner

`runner.cjs` exports `runAcceptance({cases, catalog, call, checkpoint})` for the
existing authenticated MCP host. Inject `catalog` as platform_tool_catalog and
`call` as platform_tool_call; the runner opens no network connection and accepts
no endpoint or credential. `checkpoint` must persist the sanitized report after
every attempt and throw if persistence fails. The host must enforce its tool-call
timeout and stop the run on cancellation; the runner never races a second request
against a request still in flight.

The v1 runner deliberately accepts only native `plugin.operation.http-check`
finite GET cases. It verifies operation/schema/cluster/audit binding and all four
checks before passing. Transient transport errors get at most two additional
attempts, sequentially, with 1s/2s delays. Missing backend is blocked, contract
failure is failed, and recovered cases retain every failed attempt plus
`recoveredAfterRetry`. An empty suite never passes. This is not a mutation runner.
The versioned report contains `cases[].attempts[]` and a summary; it is distinct
from the raw-envelope input format accepted by `verify_report.py`.

Run runner regression tests with:
`node --test operations/plugin-capabilities/api-acceptance/runner.test.cjs`.
