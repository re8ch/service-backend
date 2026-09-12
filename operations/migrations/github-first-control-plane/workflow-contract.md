# Reusable workflow contract

Repository workflows are thin, SHA-pinned callers of workflows in
`re8ch-infra/.github`. The central workflow accepts only typed inputs and returns
artifact digest, attestation URL, change ID and deployment status.

```yaml
name: validate
on:
  pull_request:
permissions:
  contents: read
  checks: write
jobs:
  policy:
    uses: re8ch-infra/.github/.github/workflows/infra-validate.yml@<full-commit-sha>
    with:
      component: service-backend
```

Production deployment is separate and environment-protected:

```yaml
name: deploy
on:
  push:
    branches: [main]
permissions:
  contents: read
  id-token: write
  attestations: write
jobs:
  deploy:
    environment: production
    uses: re8ch-infra/.github/.github/workflows/gitops-release.yml@<full-commit-sha>
    with:
      gitops-path: clusters/re8ch
    secrets: inherit # temporary only; remove once all consumers use OIDC
```

The production form must not use `secrets: inherit` after migration. It is shown
only to make the compatibility boundary explicit. The final reusable workflow
exchanges OIDC for a narrowly scoped broker token and opens a GitOps PR; it does
not call `kubectl apply`. Flux is the deployer.

Required checks are `contract`, `schema`, `unit`, `render`, `secret-scan`,
`dependency-review`, `supply-chain`, and `policy`. Tests upload immutable evidence
and annotate the PR. Fork-origin workflows never receive secrets or OIDC deploy
permissions. Self-hosted runners are split into untrusted-build and trusted-release
groups; no runner has a standing cluster-admin credential.

