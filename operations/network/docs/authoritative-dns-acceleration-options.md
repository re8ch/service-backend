# Authoritative DNS acceleration options

Status: decision guide. Confirm current product features, China reachability,
terms, and pricing with the vendor before purchase.

## What caching can and cannot do

End devices usually ask a recursive resolver. That resolver caches an
authoritative answer for its TTL, so every customer should not normally reach
our authorities for every request. A very low TTL increases query volume and
latency exposure; a longer TTL improves cache hit rate but delays failover.

For relatively stable endpoint sets, begin with a 300-second TTL and measure.
Use shorter TTLs only when the health/failover system can reliably exploit them.
Negative answers also cache, so an accidental empty/NXDOMAIN response can have
effects after the authority is repaired.

GeoDNS accuracy has a structural limit: with EDNS Client Subnet, an authority
may receive an approximate client prefix; without it, the authority generally
sees the recursive resolver's location. The policy must therefore include a
safe default and cannot assume every query identifies the end user precisely.

## Requirements for RE8CH

- Anycast or a similarly global authoritative footprint, including acceptable
  mainland China performance.
- Source-aware geolocation or latency routing with a default answer.
- Health-aware removal of candidates.
- API-driven, auditable record changes.
- UDP and TCP 53.
- DNSSEC and secondary/hidden-primary support where compatible.
- No requirement to expose Kubernetes or AmongCluster credentials.
- Ability to preserve RE8CH's ordered regional candidate policy.

## Practical choices

### Managed authoritative GeoDNS

A managed provider can replace the public query-serving layer while RE8CH keeps
endpoint selection desired state in Git and publishes a bounded record policy.
Amazon Route 53 officially supports geolocation, geoproximity, latency, and
IP-based routing, health checks, default records, and ECS-informed location.
It is the clearest functional reference, but cost and China-path performance
must be measured rather than assumed.

Official references:

- [Route 53 geolocation routing](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/routing-policy-geo.html)
- [Route 53 ECS behavior](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/routing-policy-edns0.html)
- [Route 53 DNS best practices](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/best-practices-dns.html)

Other low-cost providers may advertise GeoDNS, but evaluate them with the same
acceptance matrix and do not select on monthly price alone. Required tests are
authoritative latency from each region, policy expressiveness, health-check
semantics, API rate limits, DNSSEC, secondary behavior, and failure history.

### Anycast secondary in front of a hidden primary

This cheaply improves query reachability and absorbs attacks when the secondary
serves ordinary zone data. It does **not** automatically preserve a custom
per-query Python response map. The provider must independently support the same
GeoDNS semantics, or RE8CH must publish already-materialized provider policies.

The observed HE secondary behavior illustrates the limitation: it served the
transferred stale FLK answer globally instead of executing the regional logic.

### Cloud proxy/CDN for HTTP services

For eligible HTTP services, a CDN or reverse proxy can reduce dependence on
frequent endpoint-changing DNS and provide Anycast ingress. This is not a
general replacement for authoritative DNS, Kubernetes API semantics, raw
PostgreSQL, SMTP/IMAP, or AnyTLS. Cloudflare documents that proxied records use
an automatic 300-second TTL, but protocol and product constraints must be
checked per service.

Official reference:

- [Cloudflare proxied DNS TTL behavior](https://developers.cloudflare.com/dns/proxy-status/)

### Self-hosted Anycast authorities

Running PowerDNS on Helsinki, FLK, LA, and KL can improve geographic coverage
and retains complete policy control. It is not automatically cheap after
operational cost: BGP/Anycast, DDoS handling, monitoring, upgrades, and at least
two independent failure domains are required. Advertising several unicast NS
addresses is simpler than Anycast but still needs globally reliable port 53.

## Recommended staged decision

1. Restore valid per-cluster PublicEdge and authority operation first. A vendor
   cannot repair an invalid candidate model or a deployment self-lock.
2. Set a measured TTL (initially 300 seconds for dynamic service records) and
   collect direct-authority and recursive latency/cache evidence.
3. Trial one managed GeoDNS provider on a delegated test subzone. Mirror the
   CN, US West, EU central, EU north, SEA, and unknown response maps.
4. Compare it with self-hosted LA/FLK/Helsinki/KL authorities for at least one
   failure exercise and one normal traffic window.
5. Keep the provider as public query-serving infrastructure only. Git-reviewed
   service/candidate policy remains the source, and runtime credentials stay in
   the approved secret system.

## Trial acceptance criteria

- No empty response for an unknown source while a healthy default exists.
- Correct regional first choice and health-aware fallback.
- UDP/TCP 53 works from all test regions.
- Changes converge within the promised interval and are observable by API.
- DNSSEC remains valid through failover.
- Query volume and bill are predictable under normal and attack traffic.
- Removing the provider leaves a documented, tested rollback delegation.
- The service continues locally if AmongCluster or either cluster control plane
  is unavailable.
