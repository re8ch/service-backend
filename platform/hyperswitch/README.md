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

## Deliberate first-stage limits

The HelmRelease is committed suspended and has no public HTTPRoute. This makes
the chart and namespace visible to Flux without starting upstream defaults that
contain development credentials or perform install-time downloads.

Before unsuspending:

1. Mirror every upstream image into `registry.re8ch.com` and pin it by digest.
2. Provide SOPS-encrypted runtime values for PostgreSQL, Redis, Superposition,
   admin/API keys, signing keys, and connector credentials. Never commit their
   plaintext values.
3. Vendor the version-matched `superposition_seed.toml` into the declared
   ConfigMap and apply the version-matched Superposition schema through a
   reviewed migration artifact. Install-time network downloads stay disabled.
4. Render the exact chart and reject generated Secrets with upstream example
   credentials, unrestricted ingress, privileged containers, or online builds.
5. Keep the router internal and run its health/migration/dummy-connector tests.
6. Deploy the RE8CH payment bridge and fulfillment outbox consumer.
7. Only then add `/payments/v1` to the existing `api.re8ch.com` Gateway.

## Initial deployment profile

- Chart: `hyperswitch-stack` 0.2.26
- Namespace: `payments`
- Environment: sandbox
- Public ingress, Control Center, web demo, monitoring bundle, card vault,
  analytics pipeline, and UCS: disabled
- Placement: restricted-node-6dc3f140f9
- Superposition: enabled, internal only; schema migration and fallback seed are
  intentionally supplied as reviewed artifacts rather than downloaded at boot

Stripe and PayPal should initially use hosted/redirect checkout, so raw card
data never enters RE8CH services. The existing Nuclio WeChat and Alipay code can
remain active until equivalent connectors pass replay and reconciliation tests.
