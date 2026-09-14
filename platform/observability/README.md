# RE8CH production observability

This tree replaces the hand-maintained scrape section in the legacy panel
observability manifest with VictoriaMetrics Operator resources.
It contains no custom metric collector: each platform component exports its
native metrics, and each application owns its metric semantics, SLO, dashboard,
recording rules and runbook.

## Staged rollout

1. `bootstrap/` installs VictoriaMetrics Operator, OpenTelemetry Operator and
   kube-state-metrics under Flux ownership.
2. `production/` creates metrics, logs, traces, OTEL, scrape, alerting, rule and dashboard
   resources. The Flux Kustomization is intentionally suspended initially.
3. Production may be unsuspended only when all gates below pass:
   - `ceph status --format json` reports neither `HEALTH_ERR`, unfound objects,
     nor damaged/recovery-unfound PGs;
   - the `rbd-r640` StorageClass provisions and mounts a disposable canary PVC;
   - every A1/B1/restricted-node-6dc3f140f9 control-plane member is Ready;
   - the old panel VictoriaMetrics has a verified snapshot/rollback path;
   - Cilium metrics values have been rolled out independently and all Cilium
     Pods are Ready.

The production stage runs beside the old panel stack. Cutover of Grafana data
sources and retirement of the old local-path stores is a separate, reversible
change after fourteen days of observe-only alert evaluation.

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

## Grafana preview

The six dashboards are also mounted read-only into the existing production
Grafana file provider so they can be reviewed before the suspended metrics and
trace storage stage is enabled. Preview lifecycle, verification evidence and
rollback are recorded in
`runbooks/observability/grafana-preview-2026-08-25.md`.
