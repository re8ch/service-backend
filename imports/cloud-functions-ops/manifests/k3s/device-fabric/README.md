# RE8CH Device Fabric API

This directory contains the API, controller deployment and restricted-node-6dc3f140f9 USB DRA driver
deployment for devices attached through the restricted-node-6dc3f140f9/MikroTik edge. The current
trial is applied as explicit files, not through Kustomize: `crds.yaml`, then a
ConfigMap built from `apps/device-fabric-controller/controller.py`, then
`controller.yaml`, and finally the separately reviewed `canary.yaml`.

## Resource boundaries

- `EdgeDevice` is inventory: identity, placement, connection points,
  capabilities and a Secret reference. It never contains credentials.
- `DeviceService` is exposure intent. A controller should materialize a
  selectorless `Service`, controller-owned `EndpointSlice`, and the appropriate
  `HTTPRoute`, `TLSRoute`, `TCPRoute` or `UDPRoute`. It must not manually patch
  DNS. The existing edge DNS controllers remain authoritative.
- `VoiceGateway` models provider/trunk, SIP/RTP properties and line inventory.
  Call state remains in the existing restricted-node-6dc3f140f9 FreeSWITCH voice service; Kubernetes
  status is only a projection.
- USB allocation uses Kubernetes DRA (`DeviceClass`, `ResourceSlice`,
  `ResourceClaim`) through a node-local driver. `EdgeDevice` provides asset
  metadata, but is not a scheduler API.

Implementations live in `apps/device-fabric-controller` and
`apps/r640-usb-dra`. The USB driver uses the Kubernetes 1.36-aligned upstream
`kubeletplugin` helper, publishes node-owned ResourceSlices, rescans sysfs every
10 seconds, writes CDI device-node mappings, and validates presence again at
claim preparation time.

## Reconciliation contract

The future controller should:

1. Resolve `DeviceService.spec.deviceRef` and `connectionName` to one or more
   device IPs. Reject documentation ranges, loopback, multicast and unspecified
   addresses.
2. Create a selectorless ClusterIP `Service` and an `EndpointSlice` labelled
   `kubernetes.io/service-name=<service>` and
   `endpointslice.kubernetes.io/managed-by=devices.re8ch.com/controller`.
3. For `mode: Gateway`, attach a Gateway API Route to the explicit listener.
   Never allocate another public or service VIP for every device GUI: the shared
   Cilium Gateway VIP is the ingress address and hostnames select the backend.
4. Require authentication for `Authenticated`. Stable v1alpha1 supports the
   appliance's explicit upstream login challenge; `ExternalAuth` remains
   fail-closed until the installed Cilium/Gateway API external-auth behavior is
   validated. `Public` must be denied for device administration protocols.
5. Use `BackendTLSPolicy` and a CA reference for HTTPS backends. Do not silently
   disable certificate verification for self-signed appliance certificates.
6. Publish `Accepted`, `ResolvedRefs`, `Reachable`, `Programmed` and `Ready`
   conditions with `observedGeneration`. Never write desired state into status.
7. Apply Cilium policy for both ingress stages: client/world to the Gateway and
   the Cilium `ingress` identity to the external device CIDR and exact port.

For SIP/RTP, prefer private `UDPRoute`/`TCPRoute` only where the installed Cilium
and Gateway API versions support them. The voice control API stays a normal
authenticated HTTP service. Do not expose SIP, RTP, ESL, HEP or device GUIs to
the public edge by default.

## Before any deployment

- Replace all placeholder model names and RFC 5737 example addresses.
- Inventory Grandstream, China Telecom cloud voice gateway, government/enterprise
  gateway and each USB device, including firmware and stable serial identity.
- Confirm K3s supports `resource.k8s.io/v1`; otherwise keep USB DRA manifests out
  of the apply set until the control plane is upgraded.
- For the trial, cross-compile the USB node plugin locally and install it at the
  exact restricted-node-6dc3f140f9 hostPath recorded in `controller.yaml`; the controller source is a
  read-only ConfigMap. A later production packaging decision may replace these
  trial delivery mechanisms. Never build either component in the cluster.
- Observe cluster/Cilium health through the RE8CH cluster infrastructure tool,
  then canary one authenticated GUI and one voice line before expanding.
