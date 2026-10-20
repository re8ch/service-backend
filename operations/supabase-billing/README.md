# Billing Supabase

This directory defines a complete Supabase runtime dedicated to the existing
`re8ch_billing` database. It never targets `re8ch_identity` and does not reuse
Identity JWT, database, Storage, Realtime, dashboard, or service-role secrets.

The Identity Flux domain owns this runtime's lifecycle because Billing is
currently administered with User Hub organizations and identity policy. This
is controller ownership only: Billing keeps its separate database, schema,
service accounts, JWT signer, Secrets, and public contracts. Agent Flux owns
Catalog/Bifurcate and must not reconcile Billing objects.

The manifests are declared in the Identity domain, but rollout remains gated
on the database and Secret preflight below. Missing preflight material is a
hard stop; it must not be replaced with GitHub registry credentials or shared
Identity service-role keys.

## Authority and public contracts

- `billing-supabase.re8ch.com`: Auth, REST, Realtime, and Storage only.
- `billing-studio.re8ch.com`: Studio through Kong dashboard Basic Auth.
- `db-rw.re8ch.com/auth*` and the existing Identity RPC paths remain bound to
  `re8ch_identity`; `db-rw.re8ch.com/commerce*` is the stable path contract for
  the PostgREST instance bound to `re8ch_billing`.
- `db-ro.re8ch.com/commerce*` is the read-only equivalent. The current Payments
  PostgREST deployments continue serving both paths until parallel acceptance
  passes, then only their backendRefs change.
- Identity federation uses the existing Identity issuer. Billing GoTrue has
  signup disabled and uses its own signer and session store.

## Identity boundary

There is no cross-database foreign key, FDW, dblink, user mirror, or profile
cache between `re8ch_identity` and `re8ch_billing`. Commerce tables store the
canonical Identity `sub` UUID directly (`payer_subject`, owner subject, and
equivalent domain columns). `billing.current_identity_subject()` validates and
returns that UUID from the signed request claims for RLS/RPC use. When Commerce
needs mutable user, organization, or disablement data it calls the Identity API;
it does not join across PostgreSQL databases.

The Commerce/Payment API remains the token-exchange boundary. Browser clients
authenticate with Identity and receive short-lived, audience-limited business
JWTs; they never receive a Billing service-role key. This keeps the two database
roles and DSNs independent without inventing a second consumer identity model.

## Required preflight

Use the authenticated platform administrator catalog. Do not connect with
local Pigsty credentials or print Secret values.

1. Record PostgreSQL version, primary/replica state, pgBackRest/PITR health,
   `wal_level`, available extensions, migration 057, and the presence/counts
   of `payment`, `commerce`, `billing`, and `omni_ledger` objects.
2. Confirm there are no conflicting `auth`, `storage`, or `_realtime` objects.
3. Plan and apply `roles.sql` against `postgres`, then `bootstrap.sql` against
   `re8ch_billing`, in separate reviewed calls. Keep the RW role Serializable
   and the RO role transaction-read-only.
4. Register a confidential OIDC client in Identity with the exact callback
   `https://billing-supabase.re8ch.com/auth/v1/callback`.
5. Provision the independent `supabase-billing-harbor-pull` registry credential
   and `supabase-billing/supabase-billing-secrets` through the approved secret
   manager. The runtime Secret contains:

   `ANON_KEY`, `SERVICE_ROLE_KEY`, `JWT_SECRET`, `DASHBOARD_USERNAME`,
   `DASHBOARD_PASSWORD`, `BILLING_META_DB_PASSWORD`,
   `BILLING_META_DB_PASSWORD_URLENCODED`, `PG_META_CRYPTO_KEY`,
   `GOTRUE_DB_DATABASE_URL`, `PGRST_DB_URI_RW`, `PGRST_DB_URI_RO`,
   `STORAGE_DATABASE_URL`, `REALTIME_DB_PASSWORD`, `REALTIME_DB_ENC_KEY`,
   `SECRET_KEY_BASE`, `IDENTITY_OIDC_CLIENT_ID`, and
   `IDENTITY_OIDC_CLIENT_SECRET`.

Every database URI must name `re8ch_billing`. Storage uses its own 20 GiB
`rbd-r640` PVC and may be expanded but never shrunk.
6. Start GoTrue, Storage, and Realtime privately so they initialize their
   managed schemas. Then plan and apply `post-start.sql` to install the
   canonical Identity-subject claim helper, private Storage owner policies, and
   private per-user Realtime policy. Stop rollout if the managed schema shape
   differs.

## Activation and rollout

After preflight, verify restricted-node-6dc3f140f9 allocatable resources and current requests, then
allow Identity Flux to roll out the already-declared Billing resources. Flux
must apply the signed autonomous Identity repository revision.

Run internal component health and auth federation first. Then expose the two
DNS names and run REST RW/RO denial tests, cross-user Storage denial, private
Realtime topic denial, and Studio administrator access. Run payment contract
tests and ledger invariants without real funds. Only after parallel acceptance
may the existing `/commerce` routes switch backendRefs to the new RW/RO
services. The `/auth` routes must remain unchanged and bound to Identity. Remove
the old Deployments in a later cleanup commit.

Rollback restores the prior HTTPRoute backendRefs. Never roll back database
schemas by deleting payment or ledger data.
