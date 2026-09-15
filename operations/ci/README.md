# Incremental control-plane CI

`domain-catalog.yaml` owns every deployable Kustomize root. A pull request first
runs `impact.py`, publishes its exact tests and render roots in the Actions
summary, and then executes only that surface. Main pushes no longer repeat the
PR test workflow. Secret scanning and the small governance baseline remain
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
| docs only | governance baseline; no Kustomize render |
| shared CI/schema/root Flux changes or unknown manifests | fail closed to every test and root |

Deleted paths and both sides of a rename participate in impact calculation.
Local resource/component/base and generator-file references are followed across
roots; changes to a shared base select its consumers as well as its owning root.
Changed test files are selected directly. Adding a Kustomize root without an
owner is an error. Daily 02:00 China time, published releases and manual dispatch
run full regression; incremental PR checks do not replace these safety nets.

The eight legacy required check names remain present during migration. `validate`
rejects failures and cancellations, including failure of the impact planner.
GitHub-hosted execution remains active until a controlled Linux runner has been
registered and verified; existing Macmini audit is unchanged.

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
