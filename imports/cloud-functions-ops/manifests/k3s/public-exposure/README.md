# Explicit public exposure

ExternalDNS reads only Ingress objects carrying
`public.re8ch.com/external-dns=enabled`; it does not publish other cluster
services automatically. Cloudflare credentials remain in the existing
`qianwen-ops/cloudflare-pg-dns-controller-secret`.

The policy is `upsert-only`, uses a distinct TXT owner, and is restricted to
`re8ch.com`. Qwen/ESA, database role routing, regional APIs, Harbor, and other
existing records remain owned by their existing controllers.

Follow `docs/external-dns-public-exposure-test-runbook-2026-08-07.md` before
applying these manifests.

