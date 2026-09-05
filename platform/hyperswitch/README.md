# RE8CH Hyperswitch payment orchestration

This directory stages Hyperswitch as the payment lifecycle engine. It does not
own RE8CH product orders, invoices, entitlements, or fulfillment.

## Authority boundary

- FOSSBilling and each product's order projection remain the source of truth
  for what was sold, invoiced, and fulfilled.
- Hyperswitch owns provider selection and payment attempts: authorize, capture,
  cancel, refund, provider synchronization, and connector webhooks.
- The RE8CH payment bridge maps an immutable business `order_id` and `order_type`
  to Hyperswitch `merchant_reference_id` metadata. It never asks Hyperswitch to
  create product entitlements.
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
- The chart remains sandbox-only. Real Stripe, PayPal, WeChat or Alipay connector
  credentials are added separately after webhook and reconciliation testing.

## Initial deployment profile

- Chart: `hyperswitch-stack` 0.2.26
- Namespace: `payments`
- Environment: sandbox
- Public chart ingress, web demo, monitoring bundle, card vault, analytics
  pipeline, and UCS: disabled; Cilium Gateway API owns public exposure
- Control Center: enabled at `https://pay.re8ch.com`; email login disabled,
  TOTP required, sample data disabled
- Router: `https://pay-api.re8ch.com`; no credentials are stored in this file
- Placement: restricted-node-6dc3f140f9
- Database: Pigsty `hyperswitch` with dedicated owner/admin/migrator/runtime/
  read-only/PostgREST roles; the bundled PostgreSQL chart is disabled
- Superposition: enabled, internal only; schema migration and fallback seed are
  intentionally supplied as reviewed artifacts rather than downloaded at boot

Stripe and PayPal should initially use hosted/redirect checkout, so raw card
data never enters RE8CH services. The existing Nuclio WeChat and Alipay code can
remain active until equivalent connectors pass replay and reconciliation tests.
