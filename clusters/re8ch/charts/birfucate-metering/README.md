# Birfucate Metering

`birfucate-metering` is the AI-facing semantic metering layer for infrastructure
that Kubernetes/OpenCost does not fully price: Ceph, Pigsty and network behavior.
It consumes existing Prometheus-compatible observations instead of replacing
Ceph exporters, Pigsty recording rules, Hubble, Hyperglass or OpenCost.

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
