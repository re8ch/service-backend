# Network routing and cost observability

## Scope

This stack records route health, latency, traffic, price facts and route
recommendations. It is deliberately **observe-only**: it never edits FRR,
WireGuard, Cilium, Kubernetes Node addresses, DNS or cloud routing.

DN42 is governed as a separate external experimental routing domain. Its
identity, selective peering, VRF isolation and change lifecycle are defined in
`docs/dn42-network-interconnection-governance-2026-08-05.md`. DN42 peer metrics
may later feed this observe-only stack, but the advisor is not authorized to
enable a DN42 tunnel, BGP session or route.

## Data flow

1. `re8ch-network-exporter` runs as a systemd service on each public-mesh node.
   It exports WireGuard counters, FRR BGP/BFD state, selected kernel routes,
   path probes and the observed ZeroTier physical underlay.
2. VictoriaMetrics scrapes the seven exporters every 30 seconds.
3. `re8ch-routing-advisor` evaluates the versioned cost catalog and route
   policy. Hard health gates are applied before the weighted score. Five
   consecutive wins and a five-minute hold-down suppress route flapping.
4. Recommendation changes and suppressed changes are emitted as JSON ledger
   events. Metrics expose the same decision, its eligibility and its cost fact.
   `GET /recommendations` returns structured `FRRRouteMapRecommendation`
   documents with `apply: false`; it never returns executable shell commands.
5. Grafana dashboard `Network / Routing & Cost` visualizes the facts.
6. OpenCost uses the existing VictoriaMetrics instance. Its initial Kubernetes
   resource prices are zero placeholders; they must not be treated as invoices.

Exporter 0.2 performs cached active throughput probes outside the Prometheus
request path. Every 15 minutes it downloads at most 512 KiB from each eligible
peer exporter and one bounded public target, recording Mbps, duration, bytes,
success and observation time. At seven eligible peers this is below 10 GiB per
node per month. `qianwen-nuclio-79` has `throughputProbeEnabled=false` and is
neither modified nor used as an active throughput target. Stale measurements
lose eligibility after 30 minutes; they do not gate unrelated node pairs.

restricted-node-6dc3f140f9 exporter listens on `192.0.2.97:9788`. MikroTik owns
`192.0.2.183` and DNATs ZT TCP/9788 to that LAN listener. VictoriaMetrics
scrapes the MikroTik-owned service address; it must not assume that address is
a physical restricted-node-6dc3f140f9 interface.

The live OpenCost installation is chart `2.5.28`, application `1.121.0`, runs
on `rancher-panel`, and is reachable only through its ClusterIP Service. The
allocation API has discovered all 11 Kubernetes nodes. OpenCost remains the
Kubernetes resource-allocation engine. Per-node contracted bandwidth, annual
asset cost, traffic allowance and measured Cilium/Speedtest facts are exported
by `re8ch-routing-advisor` into the same VictoriaMetrics backend because the
OpenCost cloud price sheet has no native per-node bandwidth or Speedtest fields.

The 2026-08-09 catalog records the operator-supplied contracts. A 3 Mbps cloud
node is a bounded fallback, not a default bulk relay. `qianwen-nuclio-79` is
inventory-only with `routingEligible=false`. A1/B1 have zero incremental cost
because it is allocated to the restricted-node-6dc3f140f9 dedicated line. restricted-node-6dc3f140f9 is 14,000 CNY/year:
12,000 connectivity plus 2,000 annual hardware amortization.

Canonical evidence metrics are `re8ch_node_configured_bandwidth_mbps`,
`re8ch_node_monthly_traffic_limit_gib`, `re8ch_node_annual_cost`,
`re8ch_node_observed_cilium_throughput_mbps`,
`re8ch_node_observed_speedtest_download_mbps`,
`re8ch_node_routing_eligible` and `re8ch_node_public_direct_eligible`.

The advisor excludes both the source and destination of a node pair when its
catalog has `routingEligible=false`; telemetry cannot accidentally re-enable a
protected node. This is independent from readiness of every other node.

## Price authority

`manifests/k3s/network/public-mesh/network-costs.yaml` is the versioned price
fact catalog. `null` means unknown and is intentionally different from zero.
Cloud price synchronization stays disabled until a least-privilege credential
is supplied. The Alibaba adapter is declared as `aliyun-bssopenapi`; no access
key is stored in this repository. Enabling a new paid cloud product still
requires explicit approval.

`scripts/re8ch-aliyun-cost-facts.py` can call the read-only
`DescribeInstanceBill` operation or parse a captured response. It emits a
`NetworkCostFactCandidate` for review and never overwrites the catalog. Use a
RAM identity limited to bill-read operations; do not use an account root key.
Only rows with an explicit GB/GiB usage unit produce a realized per-GiB price.

## Policy authority

`routing-policy.yaml` is the policy authority. `mode` must remain
`observe-only` and `applyEnabled` must remain `false`. The advisor refuses to
start otherwise. A recommendation is evidence for an operator-reviewed FRR
route-map change, not an automated network mutation.

## Verification

```sh
python3 -m unittest tests/test_re8ch_routing_advisor.py -v
python3 -m py_compile scripts/re8ch-network-exporter.py scripts/re8ch-routing-advisor.py
```

Live acceptance queries:

```promql
sum(re8ch_frr_bgp_session_up)                       # expected 42
sum(re8ch_frr_bfd_session_up)                       # expected 42
count(re8ch_network_exporter_build_info)            # expected 7
count(re8ch_routing_recommendation_info)            # expected 42
sum(re8ch_routing_recommendation_change_needed)
sum(re8ch_network_estimated_egress_cost)
```

Unknown-price paths are visible through
`re8ch_network_cost_fact_status == 0`. Do not enable budget enforcement until
all paths that may win have authoritative price facts.

## Rollback

Delete only the advisor Deployment/Service and its three ConfigMaps, remove the
two scrape jobs, and stop `re8ch-network-exporter.service` on a node if its
collector causes a local issue. None of these actions changes the active route,
so FRR/WireGuard/ZeroTier continue operating independently.
