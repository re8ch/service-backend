# Ephemeral R640 agent runners

This component declares the outer execution boundary for RE8CH agent jobs. ARC
creates one runner Pod for one GitHub Actions job and removes it immediately
after completion. The runner is not a replacement for OpenSandbox: ARC owns the
GitHub job lifecycle, while OpenSandbox remains the inner untrusted-code
lifecycle when an agent needs to execute generated code.

The scale set is organization-scoped so it can authenticate with a least-
privilege GitHub App instead of an enterprise PAT. The App requires only
organization `Self-hosted runners: read and write` and metadata read access.
Create the `arc-re8ch-agent-github-app` Secret in `arc-runners` through the
approved secret path with keys `github_app_id`, `github_app_installation_id`,
and `github_app_private_key`. No credential value belongs in Git.

The runner uses `kubernetes-novolume` mode. Every workflow job must declare a
job container; ARC creates that job container as a separate Pod, no Docker
socket is exposed, and the runner Pod itself receives no workload credentials.
`minRunners: 0` prevents an idle credential-bearing runner from persisting.
The initial ceiling is 16 concurrent runners under a namespace quota of 32
requested/48 limited CPU cores and 96/128 GiB memory. This is derived from the
R640 node's 76 allocatable CPU cores and roughly 235 GiB allocatable memory,
not from the separate four-core `GitHub Hosted EastAsia` Azure runner profile.
That hosted-compute profile remains a private-network control and verification
path; it does not cap R640 execution capacity.

BYOK is consumed through the existing LiteLLM virtual-key and quota plane. A
job receives only a task-scoped virtual key from the approved secret broker and
uses the authenticated tenant MCP endpoint. Provider master keys, the LiteLLM
master key, MCP backend credentials, Kubernetes credentials, and GitHub App
private keys must never be exposed to a workflow job.

Chart inputs are pinned to the official ARC `0.15.1` release:

- controller chart digest: `sha256:c2497a6dedaaa723322abfe9ea2f5a81b9b8fbb90b38e45d7835b6c7629a3aae`
- runner-set chart digest: `sha256:2ae7e43db61047aa3c4182e73c8f58ace35e796fa07d41b64fb3c39e81e1f365`
- controller image index: `sha256:4b51a20db717b194e2f3fa96cfe2ddcb21602811f79053c75fd1a383517b9f90`
- runner image index: `sha256:e275409999c37162649aa050ca573f03eab2eabcb3a1e7fcc47548de6b4f9690`

Runtime delivery must use the reviewed cloud SSA executor. Do not install these
charts from a workstation or introduce a local Flux writer. Before activation,
render both charts, verify the digests above, inject only the Secret reference,
and read back the controller, listener, `AutoscalingRunnerSet`, ephemeral runner
Pod, and GitHub runner registration from their respective authorities.

