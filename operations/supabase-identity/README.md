# Supabase Auth identity migration

The deployment target is a dedicated Pigsty PostgreSQL instance/cluster,
published to Kubernetes as `pg-identity-primary.pigsty-external.svc.cluster.local`,
with a database named `re8ch_identity`. The
existing RE8CH `auth` database and the existing Supabase `postgres` database
remain in service until their consumers are migrated and verified. Do not
retarget the current Supabase stack by changing only `POSTGRES_DB`: GoTrue,
PostgREST and Storage use separate secret-held DSNs, and Studio/Meta use
`POSTGRES_DB`.

`identity-stack.yaml` is the isolated candidate for GoTrue, PostgREST,
Studio/Meta and Kong. It is deliberately not part of the live Kustomization.
It uses the new `supabase-identity` namespace and Secret, without touching the
existing Supabase stack. Provision `supabase-identity-secrets` with distinct
DSNs for `re8ch_identity` before adding this file to GitOps. The existing
Secret must not be copied verbatim because its DSNs still name `postgres`.

## Database and secret preparation

1. Observe Pigsty primary and backup/PITR health. Record schema-only and
   count-only inventories of both source databases. No credential values or
   password hashes may enter Git or logs.
2. Provision `supabase_admin`, `supabase_auth_admin`,
   `supabase_storage_admin`, `authenticator`, `anon` and `authenticated`
   roles on that instance through its Pigsty inventory and secret manager.
   From an approved admin session on the **new identity instance**, run `bootstrap.sql` with
   `psql -X -v ON_ERROR_STOP=1 -d postgres -f bootstrap.sql`. It creates only
   `re8ch_identity` and grants the existing Supabase roles access. Confirm
   the target is empty before first GoTrue startup.
3. Provision distinct Secret references for the new identity deployment.
   The GoTrue, PostgREST, Studio/Meta and optional Storage connection strings
   must all name `re8ch_identity`. Use the existing secret manager; never copy
   or print a live password to build a DSN in a shell argument.
4. Start GoTrue in isolation to initialize its schema, then apply the
   `cloud-functions/sql/025_supabase_identity_core.sql` migration. Run its
   importer preflight and transactional copy after freezing writes to the
   legacy auth database.

## Public issuer gate

The approved new issuer is `https://auth.re8ch.com/auth/v1`. Current GoTrue
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
