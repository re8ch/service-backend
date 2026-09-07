# Birfucate Metering

Birfucate exports tenant occupancy, behavioral bifurcation, resource counts,
and versioned showback cost. Each meter declares a `resourceType` and `purpose`,
which form the Tenant → Resource type → Purpose flow shown by Headlamp. Pricing
is opt-in and never posts accounting entries; missing rates stay explicitly
unpriced instead of being guessed.

`birfucate-metering` is the AI-facing semantic metering layer for infrastructure
that Kubernetes/OpenCost does not fully price: Ceph, Pigsty and network behavior.
It consumes existing Prometheus-compatible observations instead of replacing
Ceph exporters, Pigsty recording rules, Hubble, Hyperglass or OpenCost.

The chart also installs the `finops.re8ch.com/v1alpha1` control API and an
observe-only discovery controller. It materializes exactly one `ServiceShard`
and `BifurcationPath` for each Deployment, StatefulSet, DaemonSet, Job, CronJob
and NuclioFunction. Source workloads are never mutated. Draft Consumables
must be approved by an owner before a tenant ConsumptionBinding is valid.

The four API kinds are:

- cluster-scoped `Tenant` for generic admin or named-tenant identity mapping;
- cluster-scoped `Consumable` for the app-native authorization adapter and meter contract;
- namespaced `ServiceShard` for one workload and its discovered dependencies;
- namespaced `ConsumptionBinding` for tenant/service-instance allocation;
- namespaced `BifurcationPath` for compact Headlamp topology and usage status.

High-frequency UsageEvents remain in the outbox and metrics/ledger backends;
Kubernetes stores only declarative intent and bounded status summaries.

The Chart also owns a read-only `/api/v1/browser/query` endpoint for Headlamp.
It accepts only the six Birfucate metric names and constructs the bounded
`last_over_time` query itself; the browser is not given the VictoriaMetrics
address or arbitrary PromQL access.

With `tenantPlatform.enabled`, the same release owns the autonomous tenant
contract: the `tenancy.re8ch.com` CRDs, controller, digest-pinned runtime,
`admin` and `tenant` APIs, MCP backend entries, route, and tenant identity
definitions. Secret values remain external SOPS-managed Kubernetes Secrets and
are referenced by name only.

## Two charging facts

- **Occupancy** integrates a resource gauge over time: `quantity = occupied × seconds`.
  Examples are GiB-second for Ceph/Pigsty and IP-second for public network addresses.
- **Bifurcation** measures behavior: occurrences, intensity per occurrence and
  fan-out are retained independently. The neutral, unpriced score is:

  `score = occurrences × log2(1 + intensity/referenceIntensity) × max(1, fanout)`

The score is a usage fact, not money. Billing owns price curves and Accounting
owns cost allocation/journal entries. Raw dimensions accompany every event so
the downstream calculation remains explainable and replayable.

## Data flow

```text
Ceph exporters ───────┐
Pigsty recording rules├─> VictoriaMetrics/Prometheus ─> Birfucate ─> UsageEvent outbox
Hubble metrics ───────┤                                  │                │
Hyperglass adapter ───┘                                  └─ /metrics      ├─> Billing
OpenCost API ───────────── dependency/static-cost link                    └─> Accounting
```

Every source vector must carry `tenant` and `resource_ref`. Samples missing
either label are ignored; this is intentional cross-tenant leakage protection.
Hubble and Hyperglass may need tenant-label recording rules/adapters before the
default behavioral meters produce data.

## Promotion

The default image contains `IMAGE_REQUIRES_VERIFIED_DIGEST`. Build the collector
image, scan/sign it, then supply a digest-pinned `image.tag`. Render before a MR:

```sh
helm lint clusters/re8ch/charts/birfucate-metering
helm template birfucate clusters/re8ch/charts/birfucate-metering \
  --namespace observability-ai
```

Enable exactly one of `vmServiceScrape` or `serviceMonitor` unless both CRDs are
intentionally installed. The usage sink is disabled by default; enabling it
requires an out-of-band token Secret and a receiver that deduplicates `eventId`.
