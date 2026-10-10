# Historical order and ledger migration (not in the fresh-start rollout)

The 2026-10-10 rollout starts `re8ch_billing` empty and does not import old
wallets, orders, payment events, rates or journals. See `FRESH-START.md` for
the active release. This document remains the controlled procedure if a later
decision requires a historical migration.

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
  `021`, `040`–`044` and billing/commerce migrations `046`–`050` together, and
  their SQL tests passed there. Migration `048` posts a bound Credits topup
  within the payment success update and reverses it within the refund update;
  an unbound success rolls back. No production data has moved.
- The new Commerce schema now posts purchases and refunds to one `billing`
  double-entry journal inside the order transaction. Its account transfer
  primitive now supports personal and organization accounts, opening imports,
  and service spend with insufficient-balance checks. The historical Identity
  Credits outbox still writes `re8ch_identity` in the live legacy Pod. The
  target Go Pod can route Credits requests to billing RPCs behind
  `BILLING_CREDITS_ENABLED=true`, but is not deployed. Keep the old dispatcher
  active only for old traffic while its outbox drains; the target Pod must have
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
   The payment inventory must report zero event chain breaks, event hash
   mismatches, missing audit delivery rows, and audit delivery hash mismatches
   before and after moving the registry. Preserve the old event sequence and
   audit bytes exactly; do not regenerate event IDs or hashes during import.
   After source writes are frozen, export the two wallet maps and journals with
   `psql -X -v ON_ERROR_STOP=1 -f export-identity-wallets.sql`,
   `export-identity-transfers.sql`, `export-commerce-wallets.sql`, and
   `export-commerce-transfers.sql`, each against its named source database.
   Also run `export-identity-billing-records.sql` against the frozen Identity
   database. Its JSON records cover purchase rates, topups, refunds, service
   prices, and quotes; compare per-type counts with the inventory. Import these
   records in the CSV's dependency order using the migration-owner-only
   `billing.import_legacy_identity_record(type,record,tenant_id)` function
   from migration `051`. Use a protected tenant-resolution list for any
   unbound topup; `tenant_id` is otherwise derived from the migrated payment.
   The importer verifies credited topups and refunds against migrated payment
   rows and historical journal postings, and is idempotent only when all
   source fields match. Compare its per-type target counts to the frozen source
   inventory and require every mismatch in
   `billing.verify_legacy_identity_metadata()` to be zero before cutover.
   This includes succeeded Credits payments without a topup, missing journal
   postings, refund overdraw and accepted quotes without spend. A credited
   topup without a payment ID requires an explicit source investigation before
   import. Resolve each topup's tenant from the payment registry or an
   independently verified ownership record; the old topup table has no tenant
   column. Do not invent a tenant or mark an uncredited topup as paid.
   Redirect each CSV to a protected path outside Git and record its SHA-256,
   snapshot time, database identity, and row count. The export scripts emit
   owner identifiers and payment references, so restrict access to the
   migration operator. Require zero malformed transactions in the inventories,
   and match exported wallet and transfer counts to the inventory counts
   before importing anything. Preserve the Identity transfer sequence order
   and the Commerce timestamp/ID order; interpret an empty previous hash as
   empty bytes. Independently check source balance totals against the exported
   wallet rows. A live production export has not yet been obtained.
   Run `python analyze-wallet-overlap.py IDENTITY_WALLETS.csv
   COMMERCE_WALLETS.csv` on the protected exports. The JSON report contains
   aggregates only and exits nonzero if any non-system owner exists in both
   sources. Equal balances do not prove duplicate transactions: compare the
   corresponding journals and provider references before choosing an additive
   or deduplicated account mapping. Do not import an overlapping owner until
   that choice is supported by source evidence.
   After applying migrations `046`–`051` to the target and freezing old
   Commerce writes, run `commerce-snapshot.sh export OLD_COMMERCE_SERVICE
   PRIVATE_DIRECTORY` and then `commerce-snapshot.sh restore
   NEW_BILLING_SERVICE PRIVATE_DIRECTORY`. This copies the eight order-side
   Commerce tables and provider event sequence while retaining their source
   IDs. It does not copy the old Commerce wallet or credit journal tables;
   those enter `billing` through the CSV importer below. Require matching
   source/target summaries, zero order integrity mismatches, an empty target
   before restore, and no disabled Commerce triggers afterward. The script
   was exercised with a synthetic paid order and partial refund on PostgreSQL
   16; production records have not been moved.
   For a non-overlapping snapshot, the DBA can run `billing-import.sh` with
   the target `PGSERVICEFILE` alias and the five protected source CSV paths:
   Identity wallets, Identity transfers, Commerce wallets, Commerce transfers,
   and Identity billing records. Pass a sixth CSV for unbound topup tenant
   resolutions with header `legacy_topup_id,tenant_id,evidence_ref`; a
   header-only file is valid when no resolution is needed. The script first
   rejects owner overlap, then stages all CSVs and imports wallets, transfers
   and metadata in one target transaction. It requires a fresh billing target,
   exact source/target row counts and zero reconciliation mismatches. A failed
   import rolls back; retain the source CSVs and failed output for review.
   The importer also requires each restored paid order and positive Credits
   refund to match a source Commerce transfer with the same business key,
   owner and Credits amount. It rejects orphaned purchase/refund transfers.
   Use a migration-owner connection; runtime credentials must not execute the
   import functions. A synthetic end-to-end PostgreSQL 16 run passed, and a
   second run correctly refused the nonempty target. Production has not run.
2. On the exact Pigsty PostgreSQL major version and architecture, pin and test
   the Omnigres package on every database node and replica. Verify
   `pg_available_extensions` lists `omni_ledger` and its dependencies. Create
   `omni_ledger` in a disposable clone and run concurrent transfer, retry,
   PITR and replica promotion tests before any production installation. The
   isolated functional results and integration gates are in `OMNI-LEDGER.md`.
3. Create `re8ch_billing`; apply payment migrations `021`, `040`–`044` to the
   empty target. With the old payment registry frozen, a DBA with approved
   source read and target restore privileges runs `payment-snapshot.sh export
   OLD_PAYMENT_SERVICE PRIVATE_DIRECTORY`, then `payment-snapshot.sh restore
   NEW_BILLING_SERVICE PRIVATE_DIRECTORY`. The service names are entries in a
   protected `PGSERVICEFILE`; authentication comes from a protected
   `PGPASSFILE`. The private directory contains payment data and stays outside
   Git. The script requires an empty target `payment` schema, equal table
   lists, an unchanged source summary during export, a matching dump SHA-256,
   and matching source/target summaries. Restore temporarily disables triggers
   in one transaction so existing events and audit bytes retain their original
   values; the DBA must inspect `target-inventory.txt`, confirm zero integrity
   mismatches and no disabled payment triggers, then perform independent FK and
   provider reference reconciliation. A source with pending Credits outbox
   entries is not ready for cutover. The script was exercised with synthetic
   data on PostgreSQL 16; it has not moved production records.
   Apply commerce/billing migrations `046`–`051` after this payment snapshot;
   restore Commerce orders, then import and reconcile Identity and Commerce
   records before routing traffic.
   Each purchase, refund, and service spend must have a unique
   business event key and post in the same database transaction as its
   order/payment state transition. Treat `omni_ledger` as an evaluated option:
   adopt it only after testing its serializable transaction requirement,
   retries, account mapping and migration against the exact Pigsty build.
   Provider calls must never be repeated merely because a database transaction
   was retried.
4. Map Identity wallets and Commerce wallets to one canonical billing account
   per owner and unit with `billing.map_legacy_wallet`. Compare historical
   transactions and balances before import. Import each historical transfer
   with `billing.import_legacy_transfer`, preserving its source transaction ID
   and original `created_at`, and validating each Identity sequence, entry hash and previous hash against
   the original journal algorithm. Then require `billing.verify_legacy_wallets`
   to show zero owner balance mismatches for each source. The importer rejects
   a second source for the same owner account; reconcile overlap and prove
   deduplication before changing that rule. Keep provider transaction IDs
   unique across imports. Use an opening adjustment only for a documented,
   independently verified gap that cannot be represented by source entries.
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
