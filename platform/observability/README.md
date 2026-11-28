# RE8CH production observability

This tree defines the VictoriaMetrics Operator resources. It contains no custom
metric collector: each platform component exports its
native metrics, and each application owns its metric semantics, SLO, dashboard,
recording rules and runbook.

## Delivery authority

GitHub Actions and the bounded cloud SSA executor deliver both `bootstrap/` and
`production/`. Activation is determined from accepted state, read-back evidence
and runtime health.

The storage and rollout safety gates remain applicable to any replacement or
capacity migration:

- Ceph must report no `HEALTH_ERR`, unfound object or damaged/recovery-unfound
  PG;
- `rbd-r640` must provision and mount a disposable canary PVC;
- the required control-plane members must be Ready;
- the current metrics stores must have a verified snapshot and rollback path;
- Cilium metric configuration and Pods must pass their independent acceptance.

Datasource health must be proven through the tenant provider binding and real
queries against the active stores.

Current Ceph evidence and the recovery boundary are recorded in
`runbooks/observability/ceph-storage-gate-2026-08-25.md`.

## Retention and sampling

- raw metrics: 90 days in `VMCluster/re8ch-metrics`;
- Kubernetes and OTLP logs: 14 days in `VLSingle/re8ch-logs`, bounded to 90 GiB;
- recording rules: 180 days in `VMSingle/re8ch-analytics`;
- traces: 30 days in `VTCluster/re8ch-traces`;
- critical scrapes 15s, normal scrapes 30s, heavy exporters 60s;
- traces baseline head sample 10%, with errors and slow spans retained by the
  gateway tail sampler.
- Kubernetes `events.k8s.io/v1` is watched by the native OTEL `k8sobjects`
  receiver, retained in VictoriaLogs and exposed through the Event Stream
  dashboard; no custom event collector is introduced.

See `runbooks/observability/application-observability-contract.md` and
`inventory/observability-services.yaml` for the ownership and coverage model.

## Alerting and delivery management

`VMAlert/re8ch` evaluates GitOps rules and sends them to the three-replica
`VMAlertmanager/re8ch-alertmanager`. Namespace owners may declare native
`VMAlertmanagerConfig` resources; the operator-enforced namespace matcher stays
enabled, and unmatched alerts use the platform severity defaults. Receiver
credentials are referenced from Secrets and never stored in this repository.

Alertmanager routes alerts and invokes receivers. Secretary consumes its webhook
as a delivery-management observation: this proves only that the Secretary
receiver was invoked. Provider acceptance, device delivery, human attention and
responsibility acknowledgement require separate channel evidence. Missing
evidence remains unknown. See `policies/README.md` for the namespace contract.

## Shared Grafana

`production/grafana.yaml` is the reviewed desired-state definition of the shared
Grafana runtime applied by Cloud SSA. It runs one recoverable replica in
`observability-system`, retains
`https://grafana.service.re8ch.com/grafana/`, uses a dedicated PostgreSQL role/database
from `re8ch-grafana-database`, uses a Dex public PKCE client, and references a
separate renderer-token Secret. `production/grafana-secrets.sops.yaml` is the
encrypted GitOps authority for these values; plaintext credentials never enter
Git and no credential is copied from the legacy monitoring namespace.
The database runs on a dedicated persistent claim and the credential-free public PKCE client is reconciled
into Dex from the checked-in client contract.

Dex/OIDC group claims select one Grafana Org per platform or tenant group; the
checked-in org contract defines the only allowed names and roles. Datasources,
dashboards and alerts are provisioned read-only from this tree. New datasource
URLs must stay inside `observability-system`; legacy `panel-*` and tenant proxy
backends are forbidden. The old Grafana deployment remains a 1-replica rollback
target until the fourteen-day acceptance window completes, then it is frozen at
zero and retired in a separate reviewed change.
