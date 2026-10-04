# Management directory Stagehand acceptance

This pinned Stagehand 4.1.0 runner exercises the **live directory inventory**:
observe and open Native capabilities, clear/filter each captured group/version,
and verify an empty search. Every table cell is compared with the checked-in
registry; Stagehand also extracts the displayed counts. A stale deployment,
missing rows or incorrect acceptance labels fails the run.

This suite does **not** establish tool invocation, controller outcomes, failure
recovery, authorization isolation or full operation semantic coverage. Its
`inventoryNavigation` result is separate from `semanticConformance`,
`deployedAdapterInvocation` and `httpConformance`, which remain `not_run`.
It never updates the registry's acceptance flags.

## Prerequisites and execution

- Node >=22.18.0, Chrome compatible with Stagehand's local connection, and a
  dedicated authenticated browser session owned by the test operator.
- Deployed directory/UI/native registry matching this revision; working login.
- `STAGEHAND_CDP_URL`: loopback CDP endpoint for that dedicated browser. This
  runner does not launch Chrome, extract cookies, or read browser profiles.
- `STAGEHAND_MODEL` and `STAGEHAND_MODEL_API_KEY`: approved model name and secret
  supplied by the execution environment. Never paste or commit the secret.
  Stagehand receives directory page content through that model provider.
- `RE8CH_DIRECTORY_DETAIL_URL`: actual authenticated catalog detail URL under
  `https://tools.re8ch.com/catalog/<repo>/<server>/<version>`; copy it from the
  deployed directory. The optional `?registryName=approved` selector emitted by
  that directory is supported; other query parameters and registries are rejected.
  Do not invent a repository name or bypass login.

```sh
cd operations/plugin-capabilities/stagehand
npm ci --ignore-scripts
npm test
npm run acceptance
```

`npm test` is offline runner validation, **not** semantic acceptance. Missing
configuration produces a nonzero exit and `not_run`. Other execution failures
record their stage and return nonzero. No raw SDK error, DOM, screenshot,
credential or resource body is saved. Local ignored `reports/` contains only
counts, registry digest, filter names, timestamps and result flags. The runner
closes its own tab and SDK session, leaving the supplied browser running.

Use a bounded job timeout in the chosen execution environment. This runner is
not automatically executed by PR CI because CI has neither an authenticated
directory session nor an approved model connection. Its offline checks run in
the UI validation workflow. Failed-operation and backend-state scenarios remain
separate outstanding acceptance work, not implicitly passed cases.

API references: [Stagehand quickstart](https://docs.stagehand.dev/v4/first-steps/quickstart)
and [browser connection](https://docs.stagehand.dev/v4/configuration/browser).
