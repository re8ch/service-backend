# Mobile egress control

This service publishes a signed, device-consumable view of explicitly authorized
Mihomos exits. Advanced Fabric inventory supplies ASN/role/capacity facts;
Mihomos readiness supplies exit capability; PublicEdge may publish the HTTPS API
and per-exit transport hostnames but does not choose the device's path.

The projection deliberately does not turn ordinary fabric leaves into exits.
The current service implements enrollment, signed bootstrap/catalog, scoped
short-lived credentials, ETag caching, and privacy validation. The node-local
Hysteria2/Trojan gateway that validates those credentials before reaching the
private Mihomos SOCKS provider is a separate required deployment and is not
represented by the existing unauthenticated port 1080 service.

Run tests with `python3 -m unittest -v test_core.py`. Secrets must contain a raw
or base64 Ed25519 private key (`signing-key`) and at least 32 random bytes
(`credential-secret`).
