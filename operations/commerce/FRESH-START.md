# Fresh billing rollout (2026-10-10)

The first `re8ch_billing` release starts with no historical transactions. This
is a product decision, not evidence that the older Identity Credits, Commerce,
or payment registry stores were empty. Preserve those stores for their existing
traffic and audit history. Do not run `payment-snapshot.sh`,
`commerce-snapshot.sh`, `billing-import.sh`, or migrations `050`–`051` for this
release.

## Database state

The authenticated administrator `postgres_admin_sql` path created
`re8ch_billing` on the Pigsty primary and applied the reviewed
`re8ch/cloud-functions#119` migrations in this order:

`021`, `040`, `041`, `042_payment_public_verification`, `043`, `044`, `046`,
`047`, `048`, `049`.

The existing global `commerce_runtime` role cannot be delegated by the
`supabase_admin` identity because it has no admin membership. Its attempted
grant failed without changing membership. A reviewed follow-on migration
`052_billing_commerce_runtime.sql` will create a target-specific RPC role and
grant it to the Commerce PostgREST login roles. Do not route traffic through
those roles until this migration is applied and verified.

Each write used a plan hash and `apply=true`; the migration scripts' outer
`BEGIN`/`COMMIT` were removed so the tool could wrap each script in a single
transaction. Read-only inspection found the `payment`, `commerce`, and
`billing` schemas, their RPC schemas, and zero rows in `commerce.orders`,
`payment.payments`, `billing.transactions`, `billing.entries`, and
`billing.accounts`. The existing `commerce_anon`, `commerce_authenticator`,
and `commerce_ro_authenticator` roles were already present. This does not
establish application connectivity or public checkout readiness.

Before database creation, `pg_is_in_recovery()` was false, WAL archiving was
on, `pg_stat_archiver.failed_count` was zero, and pgBackRest reported repository
status `ok` with a full and differential backup. The last differential backup
was dated 2026-09-28. Recheck backup freshness and PITR before the first
payment and after new schemas have been included in a backup.

## Identity and payment boundary

- The Go Commerce API authenticates through the shared `/auth/v2/me` endpoint
  and uses the canonical GoTrue UUID as `commerce.orders.payer_subject`.
  Billing stores that UUID and never creates its own user credential.
- The Go payment registry supports a billing-backed Credits path behind
  `BILLING_CREDITS_ENABLED=true`. Its USD/Stripe top-up uses Hyperswitch and
  records the payment and Credits posting in `re8ch_billing`; a verified
  payment success fires the target billing trigger. The old cross-database
  dispatcher must be disabled in the new Pod with
  `CREDITS_DISPATCH_ENABLED=false`.
- The User Hub has a WeChat Native QR purchase section backed by Commerce and
  a separate Stripe top-up form backed by the payment Credits API. Both must
  point to the same target database before a new payment is accepted. Keep the
  old Credits balances visually separate; a fresh wallet starts at zero.

## Remaining release gates

1. Build, sign and inspect the exact Go Commerce and payment registry image
   digests through the reviewed `commerce-go-release` workflow. The Commerce
   deployment remains a template and the public `/commerce/v1/healthz` returned
   HTTP 503 on 2026-10-10.
2. Apply and verify `052_billing_commerce_runtime.sql`, then provision the
   target database's distinct RO/RW PostgREST credentials and
   runtime JWT secret through the approved Secret path. Grant only the required
   RPC roles. Keep the current payment registry Secret pointed at its existing
   store until the new Go Pod and target routes are ready.
3. Deploy the Commerce Go Pod and its two PostgREST services through a signed
   GitOps PR. Validate anonymous table denial, RO write rejection, and the
   `commerce_api` RPCs. Deploy the Go payment registry with target billing
   DSNs and the two Credits flags above; validate its Stripe/Hyperswitch
   connector and signed webhook against the new payment tables.
4. Publish explicit CNY Commerce and USD Credits purchase rates through their
   authenticated pricing-admin APIs. Never infer a price from usage data.
5. With checkout limited to operators, complete one low-value WeChat Native
   and one Stripe test/live-mode transaction as appropriate. Match each
   provider transaction ID, amount, currency, payer UUID, one order/payment
   row, and exactly one balanced Credits transfer. Replay each webhook to
   confirm idempotency. Verify refund and uncertain-payment behavior before
   exposing purchase actions to everyone.

The historical migration gates in `CONSOLIDATION.md` remain applicable only if
historical data is later brought into this database.
