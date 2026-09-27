# Supabase Auth identity migration

The deployment target is the existing `pg-re8ch` Pigsty cluster, using its
primary Service `pg-re8ch-primary.pigsty-external.svc.cluster.local` and a
separate database named `re8ch_identity`. The
existing RE8CH `auth` database and the existing Supabase `postgres` database
remain in service until their consumers are migrated and verified. Do not
retarget the current Supabase stack by changing only `POSTGRES_DB`: GoTrue,
PostgREST and Storage use separate secret-held DSNs, and Studio/Meta use
`POSTGRES_DB`.

`identity-stack.yaml` is the isolated deployment for GoTrue, PostgREST,
Studio/Meta and Kong. It is part of the cluster Kustomization but has no public route.
It uses the new `supabase-identity` namespace and Secret, without touching the
existing Supabase stack. Provision `supabase-identity-secrets` with distinct
DSNs for `re8ch_identity` before adding this file to GitOps. The existing
Secret must not be copied verbatim because its DSNs still name `postgres`.
The `supabase-identity-secrets` Secret was provisioned in the cluster with
distinct JWT signing material and all database DSNs targeting `re8ch_identity`.
The Auth image is pinned to the verified Harbor mirror of Supabase Auth v2.196.0;
the namespace has a `supabase-identity-harbor-pull` image pull Secret. Rotate its
registry credentials through the cluster secret manager.

## Database and secret preparation

1. Observe Pigsty primary and backup/PITR health. Record schema-only and
   count-only inventories of both source databases. No credential values or
   password hashes may enter Git or logs.
2. Reuse the existing `supabase_admin`, `supabase_auth_admin`,
   `supabase_storage_admin`, `authenticator`, `anon` and `authenticated`
   roles on `pg-re8ch`. From an approved admin session on its primary, run `bootstrap.sql` with
   `psql -X -v ON_ERROR_STOP=1 -d postgres -f bootstrap.sql`. It creates only
   `re8ch_identity` and grants the existing Supabase roles access. Confirm
   the target is empty before first GoTrue startup.
   Connect as `supabase_auth_admin` to `re8ch_identity` and run
   `auth-schema.sql` to create GoTrue's empty `auth` schema under that role.
3. Provision distinct Secret references for the new identity deployment.
   The GoTrue, PostgREST, Studio/Meta and optional Storage connection strings
   must all name `re8ch_identity`. Set the GoTrue DSN `options=-csearch_path%3Dpublic%2Cauth` so its migration ledger resolves in `public` and its user/provider tables resolve in `auth`. Use the existing secret manager; never copy
   or print a live password to build a DSN in a shell argument.
4. Start GoTrue in isolation to initialize its schema. Connect as
   `supabase_auth_admin` and run `auth-reference-grants.sql`, then apply the
   `cloud-functions/sql/025_supabase_identity_core.sql` migration as
   `supabase_admin`. Run importer preflight and freeze writes to the legacy
   auth database for final cutover. As `supabase_admin`, run `import-begin.sql`
   immediately before the transactional importer, and `import-end.sql` after
   reconciliation or on import failure. The temporary policies permit the
   importer to count rows through RLS and are not needed at runtime.

## Public issuer gate

The approved new issuer is `https://auth.re8ch.com/auth/v1`. The original GoTrue
v2.189.0 contains the OAuth Server routes, but supports only `openid`,
`profile`, `email` and `phone` scopes. The current ConnecTool clients request
`groups` and `offline_access`; the candidate therefore keeps the OAuth server
disabled. A newer reviewed GoTrue build must support refresh tokens, clients
must request supported scopes, and a reviewed access-token hook plus a live
GitHub team synchronization path must restore the `groups` claim and exact
resource audiences before tool cutover. Do not infer platform administrator
membership from an organization `admin` role.

Enable OAuth Server only on the isolated deployment after these contracts
exist, configure asymmetric signing keys, and assert the OIDC discovery
`issuer`, JWKS, PKCE, refresh token and userinfo behavior before modifying the
public route. Register Stalwart and the three ConnecTool public clients with
their reviewed callback URIs. Verify GitHub organization membership remains
an access condition for those clients.

Switch HTTPRoute and all client issuer declarations only after migrated user
password login, organization RLS and the entire OAuth client matrix pass.
The cutover deliberately invalidates old sessions. Preserve the legacy auth
database and reviewed old route declarations for rollback. Any production
Studio schema edit must be recorded as SQL and followed by a reviewed schema
migration PR.
