# Cube recovery preflight

Cube core is retired; the retained frontend is not a working sandbox service.
The active runtime declaration authority is `8iops/agent-gitops`. Historical
`sandbox-trials` resources in this repository must not be reactivated as a second writer.

This platform-owned, one-shot Job checks R640 CPU virtualization flags and the
presence of `/dev/kvm`. It mounts `/dev` read-only, runs unprivileged without
service-account credentials, and writes only booleans to its termination message.
It does not install a kernel, change node labels/routes, start Cube, or touch PVCs.
After signed PR review and passing CI, apply this exact manifest through the
Cloud Platform `kube.resource.apply` tool. Read the Pod termination message and
record the result before preparing the runtime recovery PR. Remove the completed
Job through the same platform path after retaining the evidence.

On 2026-10-29, a metadata-only platform query returned NotFound for
`cube-trial/cube-trial-secret`; retained CephFS PVCs remain Bound. Do not generate
replacement passwords against those old database volumes. Recovery must either
restore the original credential references or create an explicitly separate
fresh Cube data set while preserving the retained volumes.

CompoCV deployment is gated on an actual Cube sandbox create/exec/restart test.
Then provision an independent test database and verify production endpoint and
credential isolation before the Stalwart-to-Projects feedback experiment.
