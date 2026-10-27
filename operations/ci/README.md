# Incremental control-plane CI

`domain-catalog.yaml` owns every deployable Kustomize root. A pull request first
runs `impact.py`, publishes its exact tests and render roots in the Actions
summary, and then executes only that surface. Main pushes no longer repeat the
PR test workflow; a separate [unit success baseline](UNIT-VALIDATION.md) job
validates impacted units and refreshes bounded main evidence. Secret scanning and the small governance baseline remain
mandatory for every PR.

| Change | Domain tests / render |
| --- | --- |
| advanced-fabric, routing, public mesh | networking only |
| observability bootstrap/recovery/production | observability only |
| cloud-platform, runtime-anatomy, scheduling | tenant-runtime only, plus existing tenant selector |
| adapter, connectool, external-projects, hyperswitch | delivery only |
| headlamp, stalwart | operations-ui only |
| identity/access roots | identity-access only |
| metering | metering only |
| catalogs, indexes, workspace | control-plane only |
| docs or a domain-specific workflow only | governance baseline; no Kustomize render |
| shared CI/schema/root Flux changes or unknown manifests | fail closed to every test and root |

Deleted paths and both sides of a rename participate in impact calculation.
Local resource/component/base and generator-file references are followed across
roots; changes to a shared base select its consumers as well as its owning root.
Changed test files are selected directly. Adding a Kustomize root without an
owner is an error. Daily 02:00 China time, published releases and manual dispatch
run full regression; incremental PR checks do not replace these safety nets.

`control-plane-validate` rejects failures and cancellations from every selected
internal job, including failure of the impact planner. `tenant-go-validate` is a
lightweight successful router on unrelated pull requests and runs its full Go,
GitOps, differential, and image checks only for tenant-runtime changes. These two
stable aggregators replace ambiguous duplicate `validate` check names.
Portable Linux execution uses the repository-scoped R640 runner class
`[self-hosted, Linux, X64, r640, untrusted-build]`. Existing Macmini host-bound
audits remain unchanged. The R640 runner has no standing cluster credentials;
see `operations/ci/runners/README.md` for its trust and egress boundary.

Selected unit success reuse and its trust/fallback boundaries are documented in
[UNIT-VALIDATION.md](UNIT-VALIDATION.md). PRs only consume main evidence.

Module render reuse is documented in [RENDER-EVIDENCE.md](RENDER-EVIDENCE.md).

## OCI cutover boundary

The release schema is `schemas/domain-release.schema.json`; human-facing image
tags use module-local themed codenames, while Flux pins immutable digests.
Existing deployments have **not** been switched to unpublished OCI sources.
Before cutover, each root needs a signed bundle, registry access, a digest-lock
PR and successful render/health evidence. This avoids creating two reconcilers
that own the same resources or dropping the current inventory.
`operations/releases/publishers.yaml` is a separate reviewed signer allowlist;
it starts empty and is never populated from an untrusted release payload.

The promoter App is `4950282`, installation `161846815`. It has no workflow,
secrets, administration or branch-rule bypass permission. Its private key must
be stored as an Actions secret, never as a file in this repository or in chat.

## Job-level impact

The plan includes `jobs` and `checks`. Workflow job conditions consume the
former; schema/contract/render steps consume the latter. `check_paths` declares
standalone check inputs. Focused publisher changes run unit tests without
workspace, inventory, schema or render jobs. Release locks still validate schemas
and their owning roots. Kustomize dependencies select compiled bundle checks.
Full or unknown risky changes enable every check. Secret scanning, policy and
the stable aggregate gate remain mandatory. A skipped job means unaffected by
the diff, not reused successful test evidence. This uses existing Actions job
conditions and the existing impact selector; no new controller or service.
