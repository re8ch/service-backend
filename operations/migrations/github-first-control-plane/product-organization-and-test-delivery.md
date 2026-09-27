# Product organizations and tenant test delivery

Status: target contract, 2026-09-28. The five destination organizations have
been created in the Reachieve LLC enterprise. This document does not assert
that repository transfers, DNS delegation, or cluster rollout have happened.

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
| `re8ch-image` | `image-website`, `registry-website` | Image and registry product marketing; Harbor runtime remains separate |
| `re8ch-cloud` | `cluster-website`, `cloud-functions` | Cluster product site and cloud function source |
| `re8ch-observability` | `observable-website`, `grafana` | Observability product site and Grafana source |
| `re8ch-ai` | `codex-admin-plugins`, `codex-cloud-tenant-plugins`, `tool-distribution`, and the tenant marketplace repository | AI tools, plugins, and marketplace |
| `re8ch-business` | `hyperswitch`, `user-hub` | Payments and business identity products |
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

## RE8CH Auth launch gate for tenant plugins

Userhub is the interactive client of the new Supabase GoTrue identity stack at
`auth.re8ch.com`. Stalwart at `id.re8ch.com` is an upstream OIDC provider; it
does not by itself issue Kubernetes, Tenant plugin, or GitHub write grants.
Keep the existing Dex-protected plugin path in service until its replacement
passes the checks below. The identity stack runbook currently says its GoTrue
OAuth Server is disabled for ConnecTool because the client `groups`, refresh
token, and resource-audience contracts are unfinished.

1. **Link the person.** A successful Userhub login establishes a stable GoTrue
   `sub`. Require a verified mailbox and an explicit link from that `sub` to
   the approved tenant membership. Preserve the upstream provider's immutable
   subject during linking; email address equality, domain suffix, or a GitHub
   organization role alone never creates a membership or an administrator
   grant. Revocation and email changes must be reflected in the binding.
2. **Issue plugin identity.** Register each plugin client with exact redirect
   URIs, PKCE, refresh-token rotation, issuer, JWKS and resource audience.
   A reviewed access-token hook must add current tenant role and group claims
   from the membership authority. Each plugin verifies signature, issuer,
   audience, expiry and tenant selection on every request. The existing
   TenantIdentityBinding resolver maps the verified issuer/subject or group to
   one active tenant and role; the requested tenant is only a selector. Never
   feed browser supplied email or group strings to that resolver.
3. **Enforce resource writes.** The tenant plugin may request only grants
   present in that tenant's Consumable catalog and native namespace policy.
   Workload, Service, ConfigMap and Secret writes use a namespace-scoped
   identity and Kubernetes RBAC. Keep Gateway, HTTPRoute, PublicEdge, DNS and
   production routes under the reviewed broker/GitOps path. A denied Secret
   read or cross-tenant operation must remain denied even to a user who can
   edit another resource in their own namespace.
4. **Authorize GitHub separately.** A Userhub session is not a GitHub API
   credential. Install a reviewed GitHub App in each destination organization
   and grant only selected repositories and required permissions. For a
   person-initiated repository write, verify that person's linked GitHub
   identity and current repository access before requesting a short-lived,
   repository-scoped user-to-server token. For CI, use an installation token
   bound to its repository and the verified GitHub Actions OIDC run. Do not
   put a PAT, installation token or broad GitHub write capability in a
   browser, plugin manifest, PR comment or Kubernetes Secret readable by the
   tenant workload. Record actor, tenant, repository, operation and PR in the
   audit event; write desired state through a reviewed pull request.
5. **Prove isolation before publication.** With two real test accounts and
   two tenant bindings, exercise sign-in, refresh, revocation, wrong audience,
   expired token, changed email, removed GitHub membership, selected-repo
   denial, cross-tenant namespace/Secret denial, and PR-environment expiry.
   Test both a static frontend and backend Workload through the dedicated
   `test.re8ch.com` route. Keep the official hostname on the main-branch
   GitOps path. Roll back by revoking the new client and test route first;
   preserve Dex and the previous production route until the matrix passes.

Launch evidence is a linked set of signed PRs and green Actions for the token
hook/client configuration, membership synchronization, GitHub App installation
and repository selection, tenant broker/RBAC, and PublicEdge test-zone delivery.
Record external DNS, TLS, issuer/JWKS, internal workload, CDN and negative
authorization probes before switching any plugin issuer or official hostname.
