# RE8CH Headlamp Secretary

Read-only operational console for the tenant-scoped Secretary runtime. It shows
Kubernetes workload/configuration health and consumes the authenticated,
append-only Secretary event stream plus the declared and observed interaction
topology. Configuration readiness is derived only from
Deployment `secretKeyRef` metadata, so Secret objects and values are never requested
or rendered. Event access inherits the existing authenticated Headlamp administrator
session; the plugin does not ask for or persist another token.

Declared routes and observed routes remain visibly separate, with discrepancies
and evidence-adoption state shown without exposing direct channel identities.
The dashboard also reads VictoriaMetrics Operator CRDs to show the declared log
store, log collectors, rule evaluator, Alertmanager, namespace routing policies,
scrape coverage, and delivery evidence boundaries. It never reads Secret data.
