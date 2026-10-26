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
Portable Linux execution uses the repository-scoped restricted-node-6dc3f140f9 runner class
`[self-hosted, Linux, X64, restricted-node-6dc3f140f9, untrusted-build]`. Existing Macmini host-bound
audits remain unchanged. The restricted-node-6dc3f140f9 runner has no standing cluster credentials;
see `operations/ci/runners/README.md` for its trust and egress boundary.

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

## Reusing module render evidence

The existing full-regression workflow renders every owned root without reuse.
Successful scheduled/manual runs on main retain `module-render-evidence` as a
GitHub Actions artifact for 14 days. Incremental PR render jobs select artifacts
only from successful main runs of that workflow in this repository. PR runs
never publish a shared baseline. An unavailable/expired baseline is a cache miss.
The first successful main full regression after this change warms the baseline;
use the existing manual full-regression dispatch to warm it sooner.

`render_evidence.py` matches each root's local input closure, kubectl/Kustomize
client identity, Python/PyYAML/platform, validator code, regression workflow and
regression tests. Identical inputs reuse the rendered YAML and its successful
render result. Dependencies include local bases/components, patches, generators,
replacement files and OpenAPI/configuration inputs. Remote inputs, Helm chart
rendering, plugins, symlinks, missing files and unknown Kustomize fields fall back
to execution. Changes to unrelated modules do not change the key. Full CI never
loads prior evidence. Actions summaries list executed and reused roots.

Native capability gap: Actions artifacts provide immutable storage and workflow
identity, but do not discover Kustomize input closure or decide whether two roots
have identical inputs. The bounded adapter supplies only that comparison. It
uses existing `gh api`, download-artifact and upload-artifact capabilities; no
cache server, custom release planner, poller or new deployment authority.

This first reuse slice covers successful local Kustomize render validation only.
It does not reuse pytest, vulnerability scans, live readiness, or change release
publication/attestation. Existing schema, inventory, federation and ownership
checks retain their own gates. Future reuse should add separately tested input
closures rather than treating this render result as proof of all checks.
