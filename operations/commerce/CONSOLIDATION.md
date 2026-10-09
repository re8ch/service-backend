# Single order and ledger database migration

Target: one `re8ch_billing` database owns catalog prices, orders, provider
payments, refunds, accounting accounts, and Credits transfers. Identity remains
the authority for payer UUIDs and organization membership, but does not post
new billing transactions. The commerce HTTP API remains the sole public payment
surface; it runs as a small Go Deployment in `payments`.

## Current evidence

- `cloud-functions/sql/042_identity_credits_ledger.sql` defines Identity Credits
  wallets, top-ups, spend and refunds in `re8ch_identity`.
- `cloud-functions/sql/046_re8ch_commerce.sql` defines a second Credits ledger
  and all orders and provider events in `re8ch_commerce`.
- `cloud-functions/sql/021_re8ch_payment_registry.sql` defines a separate
  payment registry with attempts, refunds and an outbox; follow-on migrations
  `040`–`044` add audit delivery, verification and Credits outbox processing.
  Its live DSN is held in `payments/payment-postgrest-secrets`; inventory the
  actual database name rather than assuming it shares either database above.
- The commerce deployment is a template and checkout defaults to disabled.
  On 2026-10-10, public `/commerce/v1/healthz` and `/commerce/v1/catalog`
  both returned HTTP 503. This is a point-in-time availability observation,
  not evidence that the database has or has not been provisioned.
- The authenticated Cloud Platform inventory on 2026-10-10 showed no
  `commerce-api` Deployment or Service in `payments`. It did show one ready
  `nuclio-payment-registry-api` Deployment and one ready `payment-postgrest`
  Deployment. Pigsty's primary and `/auth/ready` routes were reachable; the
  platform tool intentionally does not expose schemas or rows. The current
  payment registry remains live and needs a separately verified cutover.
- A disposable PostgreSQL 16 database accepted the payment registry migrations
  `021`, `040`–`044` and billing/commerce migrations `046`–`048` together, and
  their SQL tests passed there. Migration `048` posts a bound Credits topup
  within the payment success update and reverses it within the refund update;
  an unbound success rolls back. No production data has moved.
- The new Commerce schema now posts purchases and refunds to one `billing`
  double-entry journal inside the order transaction. Its account transfer
  primitive now supports personal and organization accounts, opening imports,
  and service spend with insufficient-balance checks. The historical Identity
  Credits outbox still writes `re8ch_identity`, and the Credits API has not
  switched to target billing RPCs. Keep the old dispatcher active only for old
  traffic while its outbox drains; the target Pod must have
  `CREDITS_DISPATCH_ENABLED=false` before target traffic begins.
- The platform's read-only `generate_pg_tenant_database_plan` produced a
  `re8ch_billing` role/route plan. Its generic SQL grants direct table writes
  to application roles; do not apply that SQL to this payment database. The
  payment and commerce APIs must retain RPC-only grants and separate RO/RW
  credentials. No database or Secret was created by this plan call.
- In an isolated arm64 container, `ghcr.io/omnigres/omnigres-17:latest`
  provided PostgreSQL 17.11 and `omni_ledger` 0.1.3; `CREATE EXTENSION
  omni_ledger CASCADE` succeeded and installed `omni_id` 0.4.3. The image
  logged that `omni` was compiled against PostgreSQL 17.6 while running
  17.11. This smoke test does not establish compatibility with the live
  Pigsty PostgreSQL major, its node architecture, or its failover setup.

## Migration sequence and required gates

1. Inventory live `re8ch_identity`, `re8ch_commerce` and the existing payment
   registry database schemas, row counts,
   maximum journal sequence, outstanding payment attempts, credits balances,
   and pgBackRest/PITR health. Export signed, immutable reconciliation reports.
   Preserve the current databases as read-only migration sources.
   Run `inventory-identity.sql`, `inventory-commerce.sql`, and
   `inventory-payment.sql` against their respective existing databases with
   read-only credentials. Each query uses a repeatable-read snapshot and emits
   aggregate figures without owner identifiers. A missing schema is a failed
   inventory, not an empty database; record that failure separately. Capture
   backup/PITR status from the platform operator alongside these SQL reports.
2. On the exact Pigsty PostgreSQL major version and architecture, pin and test
   the Omnigres package on every database node and replica. Verify
   `pg_available_extensions` lists `omni_ledger` and its dependencies. Create
   `omni_ledger` in a disposable clone and run concurrent transfer, retry,
   PITR and replica promotion tests before any production installation.
3. Create `re8ch_billing`; apply order/payment schema and the native `billing`
   journal. Each purchase, refund, and service spend must have a unique
   business event key and post in the same database transaction as its
   order/payment state transition. Treat `omni_ledger` as an evaluated option:
   adopt it only after testing its serializable transaction requirement,
   retries, account mapping and migration against the exact Pigsty build.
   Provider calls must never be repeated merely because a database transaction
   was retried.
4. Map Identity wallets and Commerce wallets to one canonical billing account
   per owner and unit. Compare historical transactions and balances before
   import; overlapping credits must be deduplicated by source, never summed
   blindly. Keep provider transaction IDs unique across all imports. Import
   opening balances with traceable adjustment transactions and independently
   reconcile every owner balance and total debits/credits.
5. Point both PostgREST services and the Go API at the new database, keeping
   public checkout disabled. Replay signed callback fixtures, duplicate events,
   partial refunds, uncertain WeChat orders and concurrent spend. Verify
   unauthorized table access is denied and reads use the RO endpoint.
6. Use one low-value real WeChat order to reconcile merchant transaction ID,
   exact CNY amount, order, refund behavior and exactly one ledger transfer.
   Only then expose User Hub purchase. Retire the prior write paths after
   a monitored observation period; preserve historical databases and restore
   instructions until the migration is audited.

The existing SQL and the Go adapter are not yet a completed single-ledger
implementation. Do not publish the deployment template or enable checkout
until gates 1–6 have current evidence and the signed GitOps PR is green.
