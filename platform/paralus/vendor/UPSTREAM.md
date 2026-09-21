# Vendored Paralus chart

- Upstream: `https://github.com/paralus/helm-charts`
- Chart: `ztka`
- Upstream chart version: `0.3.1`
- Application version: `v0.3.0`
- Retrieved: 2026-09-21
- License: Apache-2.0

The chart is carried in the signed GitOps source because upstream `0.3.1`
renders a duplicate `app.kubernetes.io/component` key in
`templates/configmap-kratos-hooks.yaml`. Flux's strict post-renderer rejects
that YAML before install. The local `0.3.1-re8ch.2` patch removes the duplicate
label and replaces the chart's published fixed database password with a
release-owned, generated Secret that is reused across upgrades. It also emits
the external HTTPS console URL for Kratos when the chart-owned Contour and
Ingress are disabled in favor of Cilium Gateway API. Dependencies and
application images remain upstream versions.
