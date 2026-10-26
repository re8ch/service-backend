# Reusing module render evidence

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
