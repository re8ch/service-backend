# bifurcate Agent MEMORY

Bifurcate owns resource identity, dependency and neutral UsageEvent facts. All
records are tenant-owned and authorization derives the tenant from a verified
organization context. Service definitions and meters are versioned and become
immutable after publication. External provider credentials never enter this
repository or Bifurcate storage.

Billing owns prices and accounting. Bifurcate events keep
`pricingApplied=false` and `accountingPosted=false`; the metering gateway maps
their stable service class and meter to Billing without modifying the event.

## R640 public network authority

Source of truth: `operations/network/manifests/network/public-mesh/inventory.json`
is a pinned restricted reference. Resolve it using the authenticated runtime API
in `contracts/network-topology/README.md` before any public IP, route, Gateway,
listener, DNAT, eBGP, overlay or dummy-interface change. Routing/service identities
are not directly attached public endpoints. Preserve the declared provider-owned
authority and fallback distinction. Never invent a host route or promote a
fallback when the restricted source is unavailable; stop and restore access.
