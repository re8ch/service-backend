# RE8CH billing database and WeChat Native rollout

The target `re8ch_billing` database is intended to own product price versions,
orders, payment attempts, verified provider events and the RE8CH Credits ledger. Identity supplies
the payer UUID; no card or WeChat credential is written to this database. Read
`CONSOLIDATION.md` before applying this to a live system: the existing payment
registry and historical Identity Credits data need reconciliation and cutover.

1. Verify Pigsty primary and pgBackRest/PITR health. Apply `bootstrap.sql` to
   `postgres`, then apply the payment registry migrations `021`, `040`–`044`
   from cloud-functions to the empty `re8ch_billing` database. After source
   payment writes are frozen, transfer the historical payment snapshot using
   `payment-snapshot.sh` as described in `CONSOLIDATION.md`; inspect the target
   inventory before applying billing/commerce migrations `046`–`051`.
   Grant `commerce_runtime` to both commerce authenticator roles and database
   `CONNECT` to the existing payment PostgREST role after its migration. Provision
   distinct RW and RO DSNs in `payments/commerce-postgrest-secrets`, and the same
   JWT signing secret in that Secret and `payments/commerce-api-secrets`.
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
3. Build the small Go HTTP server from `cloud-functions/functions/commerce-api/Dockerfile`.
   After the image is scanned, signed and pinned by digest, replace the
   marker in `commerce-runtime.yaml.template` and move it into
   `clusters/re8ch/components/payments/`; add it to the cluster kustomization in
   a reviewed GitOps change. Provision `commerce-registry-pull`. Deploy the
   commerce API Deployment and both PostgREST services. Verify RO rejects a write RPC and RW accepts it only with a signed
   short-lived runtime JWT. Confirm anonymous table access is denied.
   The payment registry Go Pod replacement is staged in
   `payment-registry-go.yaml.template`; it preserves the current Service name
   and disables the old cross-database dispatcher. Do not replace the live
   Nuclio Deployment until the target Credits routes, source outbox drain,
   billing database DSN and signed registry image have been verified.
4. A signed-in member of `re8ch:pricing-admins` publishes a CNY Credits SKU via
   `POST /commerce/v1/admin/catalog` with `{sku,name,amount_minor,credits}`.
   There is no implicit price. Publish a low-value package for the first real
   transaction; reconcile its WeChat transaction ID, exact amount, order and
   single credit entry before exposing the User Hub purchase UI to all users.
5. Monitor failed callbacks and orders left pending beyond 15 minutes. Query
   WeChat by merchant order number before resolving an uncertain payment; never
   mark it paid from the browser redirect or a manual UI action.

Do not copy credentials from historical product deployments. The existing
Hyperswitch and Identity Credits ledgers remain independent historical systems.
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
The target payment Pod must start with `CREDITS_DISPATCH_ENABLED=false` before
accepting target traffic. Drain the old registry outbox with the old Pod and
reconcile rates, wallet mapping and historical postings before routing the
Credits API to `re8ch_billing`.
