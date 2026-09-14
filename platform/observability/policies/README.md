# Namespace monitoring policies

Each namespace may own native VictoriaMetrics Operator resources through GitOps.
Use `VMServiceScrape`, `VMPodScrape` or `VMProbe` for collection, `VMRule` for
evaluation, and `VMAlertmanagerConfig` for routing. The operator-enforced
namespace matcher remains enabled.

Every alert must provide `namespace`, `service`, `severity`, `owner`, `tier`,
`alert_layer` and `policy_id`. Receiver credentials are Secret references and
must never be committed. A direct receiver is allowed, but its success only
establishes the strongest fact its provider can actually report. It does not
implicitly establish device delivery, human attention, or acknowledgement.

Use `examples/service-policy.yaml` as the reviewable contract. A namespace with
no matching policy falls back to the platform route in
`production/alertmanager.yaml`.
