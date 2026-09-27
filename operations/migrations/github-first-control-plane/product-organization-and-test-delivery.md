# Product organizations and tenant test delivery

Status: proposed, 2026-09-28. This document records the target contract. It does
not assert that repository transfers, DNS delegation, or cluster rollout have
already happened.

## Verified starting point

- Reachieve LLC currently contains 23 GitHub organizations. The empty
  `jjk-live-app`, `lizhang-accounting`, and `motor-muscle-lab` organizations
  overlap in purpose with populated `jjklive`, `lizhang-ledger`, and
  `motor-muscle-sim`. Keep repository history and links intact while auditing
  those names; do not delete an organization as part of this design change.
- `re8ch` owns the product websites and their platform implementations today.
  `re8ch/service-backend` remains the aggregate GitOps authority. The open
  product catalog migration PR (#631) is separate work and must be reconciled
  before changing module repository URLs or gitlinks again.
- TenantNamespace reconciliation already creates a bounded namespace, quota,
  LimitRange, RoleBinding, and ingress isolation. Its native owner role grants
  namespaced workload, Service, ConfigMap, and Secret operations. It does not
  grant HTTPRoute or Gateway access. `test.re8ch.com` has no public NS
  delegation, and PublicEdge does not declare that zone yet.

## Organization boundary

An organization is the owner of related source, recurring product planning,
and reusable product artwork. Each active product organization should have
`product-prd` and `product-assets` repositories. `product-prd` carries dated
iterations, decisions, acceptance criteria, and links to implementation PRs;
its periodic update is a reviewed PR, not an automatic overwrite. The assets
repository stores source artwork, licenses, and immutable published release
references. Runtime secrets and generated CDN copies belong in neither repo.

| Destination organization | Product source to own | Role |
| --- | --- | --- |
| `re8ch-image` (proposed) | `image-website`, `registry-website` | Image and registry product marketing; Harbor runtime remains separate |
| `re8ch-cloud` (proposed) | `cluster-website`, `cloud-functions` | Cluster product site and cloud function source |
| `re8ch-observability` (proposed) | `observable-website`, `grafana` | Observability product site and Grafana source |
| `re8ch-ai` (proposed) | `codex-admin-plugins`, `codex-cloud-tenant-plugins`, `tool-distribution`, `qwen-tenant-marketplace` | AI tools, plugins, and marketplace |
| `re8ch-business` (proposed) | `hyperswitch`, `user-hub` | Payments and business identity products |
| `re8ch` | `service-backend`, shared control plane and enterprise brand assets | Aggregate GitOps and cross-organization coordination |

Inspect repository dependencies, GitHub Apps, Actions variables, package scopes,
Pages/EdgeOne origins, CODEOWNERS, and Flux source references before each
transfer. Move one related family at a time, then update module URLs, gitlinks,
workspace lock, and generated indexes in a signed control-plane PR. Existing
URLs may redirect, but production declarations must use the new canonical URL.
No product repository transfer may turn GitLab into a write authority.

## Test environment contract

```text
product PR -> reusable GitHub Actions workflow -> signed/digest-pinned artifact
           -> GitHub OIDC -> tenant test broker -> TenantNamespace
                                           -> workload + Service + ConfigMap/Secret
                                           -> test route request -> PublicEdge
                                           -> *.test.re8ch.com
product main -> reviewed service-backend release PR -> Flux -> official public route
```

1. A GitHub repository is explicitly bound to one tenant. The broker verifies
   OIDC issuer, audience, repository ID, organization ID, workflow ref, PR
   number, head SHA, and installation identity. Its decision is recorded with
   the artifact digest. It does not accept an arbitrary repository or tenant
   name supplied by the caller.
2. The tenant controller creates `<tenant>-test-<product>-pr-<number>` only
   under an approved tenant namespace policy. The namespace gets its own
   ResourceQuota, LimitRange, NetworkPolicy, labels, expiry, and RoleBinding.
   The tenant can manage namespaced Deployment/Job, Service, ConfigMap, and
   application Secret resources there. A CI identity is scoped to its one
   namespace. Secret values are not returned in PR comments or broker logs.
3. Tenant CI cannot create Gateway, edit shared listeners, write DNS records,
   or attach an arbitrary HTTPRoute. It requests a route through a broker that
   checks ownership of the namespace and Service, permits only
   `<product>-pr-<number>.<tenant>.test.re8ch.com`, and attaches it to a
   dedicated test listener. The broker owns HTTPRoute and DNS writes; the
   platform owns Gateway and the `test.re8ch.com` zone. The authoritative DNS
   answer must come from PublicEdge after parent-zone NS delegation is
   verified. A route becomes public only after internal health succeeds.
4. Static frontends publish a content-addressed bundle to a tenant/project
   prefix in the approved object store; PublicEdge advertises its CDN target
   under the same test zone. Backend traffic goes through the dedicated test
   Gateway to the tenant Service. The broker records the URL, artifact digest,
   PR, owner, expiry, and rollback target. Production CDN origins and official
   hostnames remain controlled by main-branch GitOps PRs.
5. PR close or expiry revokes the test route first, then removes ephemeral
   workloads and the namespace after a retention window. A failed cleanup is
   visible as an operations issue. Production data and persistent volumes are
   never copied into a PR environment by default.

## Delivery gates

The first pilot needs: a documented repository-to-tenant mapping; GitHub OIDC
trust configuration; a test namespace claim with quota and expiry; Harbor image
digest validation; a dedicated Gateway listener and certificate; PublicEdge
zone declaration plus Cloudflare parent NS delegation; static CDN origin and
cache purge policy; an internal and external black-box probe; and a rollback
that restores the previous route without modifying official hosts. Run one
product through three concurrent PR environments and prove that each identity
cannot read or mutate another namespace, route, DNS name, or Secret.

Organization transfers and the runtime test lane are independent rollouts.
The former changes collaboration ownership; the latter changes cluster and
public delivery authority. Each needs its own reviewable PR and validation.
