# Public edge external monitor

`public_edge_probe.py` reads all PublicEdge services from
`platform/public-edge-manager/release.yaml` and checks public DNS, TLS and the
declared HTTP path from a GitHub-hosted runner. Every HTTP 4xx or 5xx, DNS,
TLS, connection or timeout error fails the run. Redirect responses are accepted
without following them; they do not prove the destination is healthy.

The workflow is manual while the SMS provider, approved alert template and
on-call recipient are being configured. Do not add a schedule until the SMS
delivery step is implemented and an end-to-end test demonstrates that a forced
probe failure reaches the on-call phone. A five-minute schedule is the shortest
reliable GitHub Actions cadence to request, though GitHub may delay or drop
scheduled runs; a separate dead-man check is required for a strict guarantee.

The Secretary observation endpoint uses HMAC. Store its secret as the GitHub
Actions Secret `SECRETARY_EXTERNAL_PROBE_WEBHOOK_SECRET`. The current Secretary
API accepts the shared `secretary-runtime/webhook-secret`; a dedicated scoped
external-probe key should be implemented before production activation.

The authoritative restricted-node-6dc3f140f9 public route remains `192.0.2.138 -> RouterOS
DNAT/eBGP -> 192.0.2.213/Cilium Gateway`, as declared in
`operations/network/manifests/network/public-mesh/inventory.json`. The probe
uses public DNS and never targets the ZeroTier service identity or the fallback
LAN backend.
