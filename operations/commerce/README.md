# RE8CH billing database and WeChat Native rollout

`re8ch_billing` owns new product prices, orders, payment attempts, verified
provider events and the RE8CH Credits ledger. `pg-identity` remains the authority
for the payer UUID and organization membership; no payment credential belongs
in the identity database. The 2026-10-10 rollout is a fresh start: historical
Identity/Commerce Credits and payment records are not imported. Keep their old
read paths and write paths isolated until the new checkout is verified. See
`FRESH-START.md` for the live state and remaining gates. `CONSOLIDATION.md`
documents a separate historical migration procedure if that decision changes.

1. Verify Pigsty primary and pgBackRest/PITR health. The production
   `re8ch_billing` database was created on 2026-10-10. The reviewed fresh-start
   schema migrations `021`, `040`–`044`, `046`–`049` were applied in order and
   their order, payment and ledger tables were verified empty. Do not apply
   historical import migrations `050`–`051` or run snapshot/import scripts for
   this rollout. Recheck schema and backup state before opening checkout.
   The authenticated administrator self-service catalog publishes
   `postgres_admin_sql` for this database work. Use `platform_tool_catalog`
   to discover it, run read-only inventory first, and pass each reviewed SQL
   script with a PR approval ID. The first write call returns a plan hash;
   execute only with `apply=true` and that hash. The tool accepts SQL, not
   `psql` meta commands: translate `bootstrap.sql`'s `\gexec` and
   `\connect` steps into separate, reviewed calls to `postgres` and
   `re8ch_billing`. Use `transactional=false` only for `CREATE DATABASE`.
   Migrations `052_billing_commerce_runtime.sql` and
   `053_billing_commerce_pgrst_login.sql` were applied after Cloud Functions
   PRs #120 and #122 merged. The existing Commerce PostgREST login is now a
   member of the target-specific RPC role. Migration
   `056_billing_commerce_anon_role.sql` replaces the undelegable cluster-global
   anonymous role with `billing_commerce_anon`; apply and verify it before the
   runtime uses that role. Before the GitOps runtime PR merges,
   run `provision_billing_secrets.py plan` and then `apply` through the
   authenticated R640 administrator path. It creates new payment and Commerce
   Secrets for `re8ch_billing` from existing managed login credentials, with a
   read-only session option on each RO URI. It prints no credential values.
2. The Ruiqi merchant certificate for merchant `1722340397` is encrypted in
   `clusters/re8ch/secrets/commerce-ruiqi-merchant-certificate.sops.yaml` and
   supplies the merchant ID, certificate serial and private key. The API v3 key
   is encrypted in `clusters/re8ch/secrets/commerce-ruiqi-api-v3.sops.yaml`.
   The current platform certificate and its verification key are encrypted in
   `clusters/re8ch/secrets/commerce-ruiqi-platform-certificate.sops.yaml`.
   Refresh them before certificate rotation. Read the matching WeChat
   mini-program App ID from `payments/commerce-wechat-app-identities`; provision
   its App Secret in `payments/commerce-api-secrets`. Verify the App ID is
   linked to merchant `1722340397` before enabling checkout. Include a random
   `COMMERCE_RECONCILE_TOKEN` for the internal periodic reconciliation Job. Register exactly
   `https://api.re8ch.com/commerce/v1/webhooks/wechat` as the notify URL.
3. After the Cloud Functions PR is merged, run the reviewed
   `commerce-go-release` workflow on service-backend `main` with that exact
   40-character Cloud Functions `master` commit. It builds both Go Pods for
   `linux/amd64`, publishes them to the existing Harbor `hyperswitch` project,
   attaches SBOM and provenance, and signs and verifies both digests. Inspect
   the workflow's `commerce-go.images` artifact and the Harbor scan before
   using either image. The signed digests from release run `38048650285` are
   pinned in `clusters/re8ch/components/payments/`. The Commerce API reuses
   the existing `payments/harbor-hyperswitch-admin` pull Secret. Deploy the
   commerce API Deployment and both PostgREST services. Verify RO rejects a write RPC and RW accepts it only with a signed
   short-lived runtime JWT. Confirm anonymous table access is denied.
   The payment registry Go Pod replacement is staged in
   `payment-registry-go.yaml.template`; it preserves the current Service name
   and disables the old cross-database dispatcher. The nine expired historical
   `re8ch_payment` rows are failed attempts; retain them for audit without
   importing them into the fresh billing database. Do not replace the live
   Nuclio Deployment until the target Credits routes, new billing database
   Secrets and signed registry image have been verified.
4. A signed-in member of `re8ch:pricing-admins` publishes a CNY Credits SKU via
   `POST /commerce/v1/admin/catalog` with `{sku,name,amount_minor,credits}`.
   There is no implicit price. Publish a low-value package for the first real
   transaction; reconcile its WeChat transaction ID, exact amount, order and
   single credit entry before exposing the User Hub purchase UI to all users.
   Browser WeChat Native checkout is enabled with
   `COMMERCE_CHECKOUT_ENABLED=true`; the User Hub calls only
   `https://api.re8ch.com/commerce/v1` and renders the returned `code_url`
   locally. The Commerce API keeps database access server-side through
   `https://db-rw.re8ch.com/commerce` and
   `https://db-ro.re8ch.com/commerce`. Mini-program JSAPI checkout remains
   disabled independently.
5. Monitor failed callbacks and orders left pending beyond 15 minutes. Query
   WeChat by merchant order number before resolving an uncertain payment; never
   mark it paid from the browser redirect or a manual UI action.

Do not copy credentials from historical product deployments. The existing
Hyperswitch and Identity Credits ledgers remain independent historical systems.
The following import procedure is retained only for a separately approved
historical migration and is not part of the fresh-start rollout.
Migration `048` adds target-only Credits topups and payment/refund triggers;
`049` adds wallet, pricing, history and quote RPCs. The Go registry Pod routes
Credits requests to those RPCs only with `BILLING_CREDITS_ENABLED=true`;
`050` adds migration-owner-only wallet and transaction import functions with
source IDs, sequence numbers and verified Identity hash-chain proofs. Import
source transactions in order and require `billing.verify_legacy_wallets` to
report zero owner balance mismatches before switching traffic.
`051` imports historical Identity purchase rates, service prices, topups,
refunds and quotes only after the payment snapshot and journal import. Match
its counts to the frozen source inventory and require zero mismatches from
`billing.verify_legacy_identity_metadata()` before moving traffic.
For a fresh target and reconciled wallet ownership, `billing-import.sh` stages
all protected source CSVs and runs those imports in one transaction; see
`CONSOLIDATION.md` for input order and tenant-resolution evidence.
Restore the old Commerce order-side tables first with `commerce-snapshot.sh`
after migrations `046`–`051`. Its source/target summary and integrity checks
cover orders, attempts, provider events and refunds. `billing-import.sh` then
requires the paid orders and positive Credits refunds to match the imported
Commerce journal by business key, payer and amount. Both scripts require DBA
access through protected PostgreSQL service aliases; no production restore has
been performed.
The target payment Pod must start with `CREDITS_DISPATCH_ENABLED=false` before
accepting target traffic. Drain the old registry outbox with the old Pod and
reconcile rates, wallet mapping and historical postings before routing the
Credits API to `re8ch_billing`.
