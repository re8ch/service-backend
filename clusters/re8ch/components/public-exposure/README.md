# Explicit public exposure

ExternalDNS reads only Ingress objects carrying
`public.re8ch.com/external-dns=enabled`; it does not publish other cluster
services automatically. Cloudflare credentials remain in the existing
SOPS-managed ExternalDNS credential Secret.

The policy is `upsert-only`, uses a distinct TXT owner, and is restricted to
`re8ch.com`. Static service and node-identity records are declared in
`static-service-external-dns.yaml`. Regional tenant/API traffic steering,
database read/write role routing, Aliyun ESA, and NetBird regional relay
selection remain owned by their specialized controllers.

The legacy `cloudflare-panel-dns-controller` is retained at zero replicas only
as rollback material after its A records and ExternalDNS TXT ownership records
were handed off. Do not scale it up alongside ExternalDNS during normal
operation.

Follow `docs/external-dns-public-exposure-test-runbook-2026-08-07.md` before
applying these manifests.
