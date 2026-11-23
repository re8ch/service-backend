# Organization-owned cluster binding authority

`pg-billing`/Bifurcate owns the durable association between a provider-resolved
`cluster_ref` and an organization. A binding contains an `owner`, `operator`, or
`viewer` role, a `Ready` or `revoked` lifecycle, and a non-secret `sha256:` binding
digest. Personal organizations use the identical model.

Identity remains the authority for users, active organizations, and memberships.
These RPCs consume a short-lived audience-limited token containing `sub`,
`org_ref`, `org_role`, and `org_membership_state=active`. There is no caller-supplied
organization argument: prompt, body, or header organization overrides have no
effect. The API gateway/PostgREST authentication entrypoint must verify the token
signature, expected issuer, Billing audience, and expiry before populating
`request.jwt.claims`; that PostgreSQL setting is not independently trustworthy.
The token exchange must revalidate membership before issuing these claims.

- Registration is limited to active organization owners/admins and is idempotent
  by organization operation key. The downscoped token must also attest
  `cluster_binding_action=register`, the exact `cluster_ref`, and the exact binding
  digest after the provider handshake; callers cannot invent another connection.
  A reused key with different input is rejected.
- A cluster reference can have only one organization binding. Adoption or transfer
  is deliberately absent from this contract.
- Revocation requires the active owning organization and exact current digest.
- Cluster operations call `verify_active_org_cluster_binding` with the minimum
  binding role they need. The function verifies active membership claims, owning
  organization, `Ready` state, and role hierarchy before returning the digest.
  Effective permission is the intersection of membership and binding roles:
  owner/admin membership permits up to owner, member up to operator, and viewer
  only viewer; the binding can reduce that ceiling further.
  The dispatcher must compare that digest with the provider-resolved connection;
  the database response is not a kubeconfig or connection credential.
- Tables have RLS enabled and no direct authenticated access. No kubeconfig,
  credential, endpoint, request arguments, or operation results are stored.

Apply `org-cluster-bindings.sql` only through the reviewed `postgres_admin_sql`
workflow against `re8ch_billing`. Git deploys this schema and policy contract; it
does not create one GitOps object per binding. Existing Admin `self` and `Atlantic`
connections are not changed by this migration. A later adoption operation must
resolve their canonical cluster references and digests, preview ownership, and
write runtime bindings without silently transferring them.
