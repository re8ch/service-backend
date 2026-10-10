# `omni_ledger` evaluation for the billing database

`omni_ledger` is a candidate accounting engine for `re8ch_billing`. The
current target implementation uses the native `billing` double-entry journal
in cloud-functions migrations `046`–`051`. No production extension has been
installed and no runtime writes to `omni_ledger` are enabled.

## Isolated result, 2026-10-10

The cached `ghcr.io/omnigres/omnigres-17:latest` image ran PostgreSQL 17.11.
`CREATE EXTENSION omni_ledger CASCADE` installed `omni_ledger` 0.1.3 and
`omni_id` 0.4.3. Startup warned that `omni` was compiled against PostgreSQL
17.6. In this disposable database:

- An issuance account with `debits_allowed_to_exceed_credits=true` and an
  owner account with `credits_allowed_to_exceed_debits=true` were created in
  one ledger.
- A transfer at the default isolation level failed with `Ledger transfers
  must be done in a serializable transaction`.
- The same 100-unit transfer in a Serializable transaction succeeded;
  `omni_ledger.account_balances` showed issuance `-100` and owner `100`.
- A 101-unit debit from that owner in a Serializable transaction failed with
  `debit exceeds credit`.
- The extension also exposes immutable account categorizations and category
  types `asset`, `liability`, and `equity`. This has not been mapped to the
  product's accounting policy.

These observations agree with the [Omnigres ledger core documentation](https://docs.omnigres.org/omni_ledger/ledger_core/),
which requires Serializable transactions for transfers. They are a functional
smoke test on one disposable image, not a compatibility or failover test for
the live Pigsty cluster.

## Integration gate

1. Read the live Pigsty major version, architecture, primary and replica
   package versions through an approved DBA channel. Install a pinned build on
   a disposable clone with the same binaries. Test extension creation,
   transfer, backup restore, replica replay and promotion. Resolve the 17.6
   build warning against the exact target PostgreSQL build before production.
2. Define one Credits ledger and a one-to-one mapping from each canonical
   `billing.accounts.id` to an Omni account. Model issuance, owner balances,
   service revenue and refunds with explicit debit/credit allowances and an
   approved chart of accounts. Historical source IDs, Identity hash proofs and
   payment references must stay queryable.
3. Make the **outer** database transaction Serializable before its first
   statement. The current PostgREST RW requests and Go RPC client do not
   establish this guarantee. Implement bounded retries for PostgreSQL `40001`
   at the database-operation boundary, preserving the business idempotency
   key. A retry must never repeat a WeChat or Hyperswitch provider call.
4. Replace the native posting path atomically. A payment status change and
   Omni transfer must commit or roll back together; do not dual-write two
   authoritative ledgers. Backfill and reconcile every historical transfer,
   owner balance and refund before changing the balance read path.
5. Repeat duplicate callback, concurrent spend, partial refund, PITR and
   replica promotion tests against the exact target build. Require signed
   GitOps review and a rollback plan before production enablement.

The target Pigsty version and authorized production database inspection are
not currently available through the published platform tool catalog. Until
the gates above pass, the native `billing` journal is the single target ledger
for the pending migration.
