# Nine-domain protected environment bootstrap evidence

Observed on 2026-10-24 while preparing the nine-domain StaticRelease authority
split.

| Domain | Repository | Environment |
| --- | --- | --- |
| agent-runtime | `re8ch-ai/agent-gitops` | `release-executor-agent-runtime` |
| identity-access | `re8ch/identity-gitops` | `release-executor-identity-access` |
| observability-metering | `siren-observer/observability-gitops` | `release-executor-observability-metering` |
| networking | `pillars-infra/network-gitops` | `release-executor-networking` |
| data-storage | `re8ch/service-backend` (bootstrap) | `release-executor-data-storage` |
| commerce-billing | `re8ch/service-backend` (bootstrap) | `release-executor-commerce-billing` |
| delivery-products | `re8ch/service-backend` (bootstrap) | `release-executor-delivery-products` |
| operations-ui | `re8ch/service-backend` (bootstrap) | `release-executor-operations-ui` |
| tenant-runtime | `re8ch/service-backend` (bootstrap) | `release-executor-tenant-runtime` |

Every environment was read back with the following protection contract:

- required reviewer: `Woomou`;
- deployment branch: `main` only;
- administrator bypass: disabled;
- environment secret: `KUBE_API_CA_PEM`;
- self review prevention: disabled until a second eligible reviewer exists.

The CA value was copied without printing it from the local `icp-k3s-direct`
cluster trust configuration after successfully verifying
`https://kube-api.re8ch.com:6443/version`. It is trust material, not a bearer
credential.

Both `k8s-admins` and `gitops-breakglass` currently contain only `Woomou`; the
team reviewer was not accepted for the service-backend environments. Requiring
`prevent_self_review` would therefore make every deployment impossible. Add a
second eligible reviewer before enabling self-review prevention.

The five service-backend environments are bootstrap targets only. When each
main domain's independently owned GitHub repository mapping is verified, create
the equivalent protected environment in that repository before moving its
authority state from `inventory` to `shadow`.
