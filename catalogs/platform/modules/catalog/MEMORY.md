# catalog Agent MEMORY

Catalog is the tenant-facing commercial control plane at `catalog.re8ch.com`.
Identity owns users, memberships and the active organization; Billing owns
products, prices, quotes, subscriptions, usage ratings and Credits allocation.
Bifurcate, ConnecTool and AmongClusters own resource identity and health.

The browser calls only `/catalog/v1` through the Catalog BFF. Never put a
Supabase service-role credential in static assets, accept a browser tenant
header as authorization, store provider credentials, or infer public network
authority from an external asset address.

Deploy only an immutable, signed image after the product PR merges and its
release workflow publishes the verified digest. This module intentionally does
not auto-import other workspace modules as products.

## R640 public network authority

Source of truth: `operations/network/manifests/network/public-mesh/inventory.json`
is a pinned restricted reference. Resolve it using the authenticated runtime API
in `contracts/network-topology/README.md` before any public IP, route, Gateway,
listener, DNAT, eBGP, overlay or dummy-interface change. Routing/service identities
are not directly attached public endpoints. Preserve the declared provider-owned
authority and fallback distinction. Never invent a host route or promote a
fallback when the restricted source is unavailable; stop and restore access.
