# RE8CH commerce database and WeChat Native rollout

The new `re8ch_commerce` database owns product price versions, orders, payment
attempts, verified provider events and the RE8CH Credits ledger. Identity supplies
the payer UUID; no card or WeChat credential is written to this database.

1. Verify Pigsty primary and pgBackRest/PITR health. Apply `bootstrap.sql` to
   `postgres`, then apply `cloud-functions/sql/046_re8ch_commerce.sql` to the new
   database. Grant `commerce_runtime` to both authenticator roles. Provision
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
