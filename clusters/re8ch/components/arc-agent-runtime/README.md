# Ephemeral self-hosted agent runners

This component declares the outer execution boundary for RE8CH agent jobs. ARC
creates one runner Pod for one GitHub Actions job and removes it immediately
after completion. The runner is not a replacement for OpenSandbox: ARC owns the
GitHub job lifecycle, while OpenSandbox remains the inner untrusted-code
lifecycle when an agent needs to execute generated code.

The scale set is organization-scoped so it can authenticate with a least-
privilege GitHub App instead of an enterprise PAT. GitHub does not support
GitHub App authentication for enterprise-scoped ARC runner scale sets; moving
the registration URL to `https://github.com/enterprises/re8ch` would therefore
require a classic PAT with `manage_runners:enterprise` and a separate reviewed
credential contract. The App requires only organization
`Self-hosted runners: read and write` and metadata read access. Create the
`arc-re8ch-agent-github-app` Secret in `re8ch-arc-runners` through the
approved secret path with keys `github_app_id`, `github_app_installation_id`,
and `github_app_private_key`. No credential value belongs in Git.

The runner uses `kubernetes-novolume` mode. Every workflow job must declare a
job container; ARC creates that job container as a separate Pod, no Docker
socket is exposed, and the runner Pod itself receives no workload credentials.
`minRunners: 0` prevents an idle credential-bearing runner from persisting.
The controller, listener and runners select
`scheduling.re8ch.com/agent-runtime=true`, not a hostname. R640 is the first
member of that pool; additional self-hosted Kubernetes nodes join by receiving
the same reviewed label and compatible taint/toleration contract. The initial
ceiling is 16 concurrent runners under a namespace quota of 32 requested/48
limited CPU cores and 96/128 GiB memory. The separate four-core
`GitHub Hosted EastAsia` Azure profile remains a private-network control and
verification path; it does not cap the self-hosted pool.

BYOK is consumed through the existing LiteLLM virtual-key and quota plane. A
job receives only a task-scoped virtual key from the approved secret broker and
uses the authenticated tenant MCP endpoint. Provider master keys, the LiteLLM
master key, MCP backend credentials, Kubernetes credentials, and GitHub App
private keys must never be exposed to a workflow job.

GitHub Enterprise Custom Models still requires a Copilot seat even when its
provider key is customer-owned. The seat-independent runtime therefore uses the
official Copilot CLI in BYOK mode inside the ARC job. Model traffic goes
directly to the public LiteLLM endpoint and the CLI runs with GitHub model
authentication and telemetry disabled. GitHub remains the dispatch,
governance, checks and audit authority; the job may receive a separate bounded
GitHub App token only when a task needs GitHub API access.

Chart inputs are pinned to the official ARC `0.15.1` release:

- controller chart digest: `sha256:c2497a6dedaaa723322abfe9ea2f5a81b9b8fbb90b38e45d7835b6c7629a3aae`
- runner-set chart digest: `sha256:2ae7e43db61047aa3c4182e73c8f58ace35e796fa07d41b64fb3c39e81e1f365`
- controller image index: `sha256:4b51a20db717b194e2f3fa96cfe2ddcb21602811f79053c75fd1a383517b9f90`
- runner image index: `sha256:e275409999c37162649aa050ca573f03eab2eabcb3a1e7fcc47548de6b4f9690`

Runtime delivery must use the reviewed cloud SSA executor. Before activation,
render both charts, verify the digests above, inject only the Secret reference,
and read back the controller, listener, `AutoscalingRunnerSet`, ephemeral runner
Pod, and GitHub runner registration from their respective authorities.

