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

Self-registered tool publication and unpriced usage intake use the separate
[`bifurcate` authority contract](TOOL-PUBLICATION-AUTHORITY.md). Those runtime
writes are organization-owned Billing state and never require a GitOps edit.

Organization-owned cluster associations use the reviewed
[`org-cluster-bindings.sql`](org-cluster-bindings.sql) runtime authority contract.
Its API and adoption boundaries are documented in
[`ORG-CLUSTER-BINDINGS.md`](ORG-CLUSTER-BINDINGS.md). Binding rows are runtime
writes in Billing/Bifurcate and are not emitted as one GitOps object per cluster.

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

## Ownership repair and migration authority

Legacy Billing objects created by `postgres` or `supabase_admin` must be
normalized once before later Catalog migrations. Use the administrator-only
`repair_re8ch_billing_ownership` tool with its plan/apply hash workflow and a
reviewed PR reference. The operation is fixed to `re8ch_billing` and the
`billing`, `payment_api`, `commerce`, and `commerce_api` schemas; it does not
accept arbitrary SQL or credentials.

The repair creates the NOLOGIN roles `re8ch_billing_owner` and
`re8ch_billing_migrator`, transfers only legacy business objects owned by
`postgres` or `supabase_admin` to the owner, and grants the migrator through
the existing server-side platform identity. Subsequent `postgres_admin_sql`
writes to `re8ch_billing` execute after `SET ROLE re8ch_billing_migrator`.
Read-only inspection remains under `supabase_admin`. Do not use
`REASSIGN OWNED BY postgres`, because it can capture extension or unrelated
objects in the same database.

`runtime-authority-migrations.yaml` packages the reviewed Tool Publication and
Org Cluster Binding SQL into versioned, retry-bounded Jobs. A schema change
requires a new Job/ConfigMap version; ordinary publication, installation,
binding, usage, and organization-cluster registration remain runtime database
writes and never create a per-registration GitOps object.

The Jobs run in `supabase-pg-re8ch` and reuse the existing managed
`supabase-pg-re8ch-secrets` keys `POSTGRES_USER_READ_WRITE` and
`POSTGRES_PASSWORD`. That is the same server-side `supabase_admin` path used by
the reviewed administrator workflow; the SQL immediately sets the bounded
NOLOGIN role `re8ch_billing_migrator`. No second password, copied Secret, or
hand-built database URI is introduced. Rotation remains owned by the existing
Supabase credential workflow.

PostgREST continues to expose only `payment_api,commerce_api`. Org Cluster
Binding is available through its three `commerce_api` RPCs. The raw
`bifurcate` schema is intentionally not exposed: Catalog/Bifurcate services use
the narrowly granted `billing_commerce_runtime` projection and
`accept_usage_event` function through their server-side database connection.
6. Start GoTrue, Storage, and Realtime privately so they initialize their
   managed schemas. Then plan and apply `post-start.sql` to install the
   canonical Identity-subject claim helper, private Storage owner policies, and
   private per-user Realtime policy. Stop rollout if the managed schema shape
   differs.

## Activation and rollout

After preflight, verify R640 allocatable resources and current requests, then
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
