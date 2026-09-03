# ConnecTool Helm chart

This chart defines one reviewable trust chain:

`Codex plugin -> Dex public PKCE client -> ToolHive vMCP -> reviewed MCP workloads`

It creates ToolHive `MCPGroup`, `MCPOIDCConfig`, and `VirtualMCPServer`
resources plus three non-secret contracts: Dex public clients, ToolHive Registry
sources, and Codex marketplace/plugin metadata. Skills are distributed by the
Git-backed Codex marketplace; ToolHive distributes MCP capabilities. Exactly
one plugin must own each MCP registration, while any number of skill-only
plugins may reuse it.

The chart does not mutate the Dex configuration Secret, install plugins on a
desktop, or store OAuth tokens. Reconcile the generated contracts through the
existing secret-aware Dex process and the authoritative Codex marketplace.

```sh
helm lint . -f examples/re8ch-values.yaml
helm template connectool . -n connectool -f examples/re8ch-values.yaml
```

## Artifact Hub and OCI delivery

The release artifact is `oci://registry.re8ch.com/helm/connectool`. A release
tag must be `connectool-v<Chart.yaml version>`. CI packages the chart, pushes
the immutable SemVer OCI tag, signs its digest with GitLab workload identity,
verifies that signature, and publishes repository metadata at the special
`artifacthub.io` tag using the Artifact Hub OCI media types.

The public Artifact Hub repository is registered as `ConnecTool` with ID
`1bbd015f-2e46-494f-a2e2-a03cabbb5d31`. Verified Publisher becomes active
after CI publishes this chart's `artifacthub-repo.yml` to the OCI metadata tag.
Registry credentials use protected, masked `CONNECTOOL_USERNAME` and
`CONNECTOOL_PASSWORD` variables. The release uses GitLab OIDC instead of a
stored signing key. Flux selects `0.1.x` and verifies that keyless identity.
