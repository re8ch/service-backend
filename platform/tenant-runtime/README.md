# Tenant runtime

The tenant control application has exactly two security planes:

- `admin` provisions `Tenant`, immutable `TenantIdentityBinding`, and bounded
  `TenantGrant` objects.
- `tenant` exposes every tenant-consumable capability, including compute and
  storage claims, at `https://tools.re8ch.com/tenant/mcp`.

There are no tenant-specific deployments or authorization groups. Each request
resolves the authenticated issuer and subject hash through
`TenantIdentityBinding`; the controller then enforces the matching active
`TenantGrant`. A tenant name, email, or group claim is never trusted as tenancy.

Promotion requires a signed immutable runtime image, the internal claim service
token, a healthy supply-chain verifier, and the canonical ConnecTool publication
in `platform/connectool/release.yaml`. Tenant onboarding separately creates the runtime namespace, workload
ServiceAccount, Harbor pull Secret, quota backstops, `Tenant`, identity binding,
and grant. No concrete tenant belongs in this platform overlay.
