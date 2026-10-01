# RE8CH Hyperswitch payment orchestration

This directory stages Hyperswitch as the payment lifecycle engine. It does not
own RE8CH product orders, invoices, entitlements, or fulfillment.

## Authority boundary

- Each product remains the source of truth for what the tenant intends to buy
  and for fulfillment. The shared registry records only payment facts.
- Hyperswitch owns provider selection and payment attempts: authorize, capture,
  cancel, refund, provider synchronization, and connector webhooks.
- The RE8CH payment registry maps an immutable product business reference to a
  Hyperswitch payment. It never owns a product catalog, invoice, or entitlement.
- A verified terminal payment event is persisted and acknowledged before a
  separate fulfillment worker consumes it. Webhook retries therefore cannot
  issue duplicate entitlements.

## Bootstrap controls

- Router keys are supplied only through
  `clusters/re8ch/secrets/hyperswitch-runtime-values.sops.yaml`. PostgreSQL is
  the shared Pigsty `hyperswitch` database reached through the reconciled
  `pg-re8ch-primary.pigsty-external.svc.cluster.local` primary Service. Its
  role passwords live only in `payments/hyperswitch-pigsty-credentials`;
  plaintext is never committed.
- `superposition_seed.toml` is vendored from Hyperswitch `v1.126.0` with SHA-256
  `dfde263a0d59561c79f18aaee654aa90505554e7f6594e790b063f74e289e91a`.
- `superposition.sql` is vendored from Superposition `v0.112.0` with SHA-256
  `e1f9cdae07ee7fb3852de172ce497c6c192944ca0f2cc0c13bf6d18de39319d9`.
  A one-shot Job waits for PostgreSQL and applies this local artifact, so initial
  installation does not depend on GitHub availability.
- A one-shot bootstrap Job idempotently creates the `re8ch` merchant and its
  organization before creating `liuhualin@wattman.cn`. Control Center has TOTP
  enabled; complete enrollment on the first login and then rotate the bootstrap
  password. The enrollment seed is intentionally never written to Job logs.
- The live profile uses production Router and Superposition settings. Payment
  connector credentials remain merchant managed in Control Center.

## Initial deployment profile

- Chart: `hyperswitch-stack` 0.2.26
- Namespace: `payments`
- Environment: production
- Public chart ingress, web demo, monitoring bundle, card vault, analytics
  pipeline, and UCS: disabled; Cilium Gateway API owns public exposure
- Control Center: enabled at `https://pay.service.re8ch.com`; email login disabled,
  TOTP required, sample data disabled
- Public API: `https://pay-api.service.re8ch.com/v1` reaches the RE8CH payment registry;
  unmatched paths continue to reach the internal Hyperswitch router. No
  credentials are stored in this file.
- Router source: RE8CH Hyperswitch fork commit
  `d6cfcb2c51861918b4bc2f4a351424e77d16475d`; the deployment is pinned to the
  immutable linux/amd64 image manifest
  `registry.re8ch.com/hyperswitch/hyperswitch-router@sha256:10a106c6a144633613529959a6a2f5edcd54fd5786e1ca3e722e27d87c958eb6`.
  The `payments/harbor-hyperswitch-admin` pull secret is managed out of band by
  the tenant credential broker and is never stored in Git.
- Placement: restricted-node-6dc3f140f9
- Database: Pigsty `hyperswitch` with dedicated owner/admin/migrator/runtime/
  read-only/PostgREST roles; the bundled PostgreSQL chart is disabled
- Superposition: enabled, internal only; schema migration and fallback seed are
  intentionally supplied as reviewed artifacts rather than downloaded at boot
- Superposition image: the production-capable `re8ch-business/superposition`
  build is mirrored into the existing private Harbor project. The deployment
  uses the build-specific tag `re8ch-45fa7d15222dce436f7851e1bb32a30f550966db`
  with `imagePullPolicy: Never` while Harbor's blob redirect remains slow from
  restricted-node-6dc3f140f9. Before scheduling this Pod on a restored node, seed the verified
  linux/amd64 Harbor manifest digest
  `sha256:cedca5587ded9007697faa2379d2c5b65342dacf55115c980162794167b58ccc`
  into its containerd image store under that tag. The
  `harbor-hyperswitch-admin` pull secret remains outside Git.
- Superposition production secrets: `DB_PASSWORD` and `SUPERPOSITION_TOKEN` are
  `local:v1` ciphertext in `payments/hyperswitch-superposition-prod-secrets`.
  The 32-byte AES key stays on restricted-node-6dc3f140f9 at
  `/var/lib/re8ch/hyperswitch-local-key/key` and is mounted read-only through
  `hyperswitch-superposition-local-key-r640`. Back up and protect this key
  separately; losing it prevents Superposition from decrypting its credentials.

Stripe and PayPal should initially use hosted/redirect checkout, so raw card
data never enters RE8CH services. The existing Nuclio WeChat and Alipay code can
remain active until equivalent connectors pass replay and reconciliation tests.

## Hosted checkout SDK

Payment links use a self-hosted Hyperswitch Web SDK at
`https://pay-api.service.re8ch.com/web/0.129.0/v1/HyperLoader.js`. The image is
built outside the cluster from `juspay/hyperswitch-web` tag `v0.129.0` (commit
`e146cc698f472c3e705384f2e9d8c0b1975932be`) and its pinned `shared-code`
gitlink `4d416578d422dba72d82d49078a5da9df8b7c2da`. Build with
`npm ci --ignore-scripts`, `npm run re:build`, and `npm run build:sandbox`, setting
`ENV_BACKEND_URL=https://pay-api.service.re8ch.com`. The upstream Helm image's
URL rewrite is applied to `HyperLoader.js` and `app.js` at image build time so
SDK assets load from the same public host. The image is signed and pinned by
digest in `sdk-runtime.yaml`; the chart's cluster-side SDK build remains disabled.
After rewriting `app.js` for the public backend URL, regenerate its SHA-384
integrity value in `index.html` before packaging the image.

## China connector live profile

- `alipaycn` uses Alipay's production gateway. Keep its merchant App ID, RSA2
  application private key and Alipay public key in Control Center.
- `wechatpaycn` uses the API v3 endpoint. Verify merchant signatures,
  notification decryption, replay rejection and reconciliation against a
  dedicated low-value order before routing live traffic to it.
- Provider notifications terminate at `pay-api.service.re8ch.com`; fulfillment remains
  a separate idempotent consumer and is never executed directly by a connector.
