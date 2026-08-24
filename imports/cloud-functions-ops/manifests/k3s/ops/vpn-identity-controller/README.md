# VPN Identity Controller

This controller is the RE8CH bridge between MikroTik IKEv2 identities and Fleet
Apple MDM profile distribution.

It is deliberately separate from Fleet internals:

```text
desired VPN users
  -> vpn-identity-controller
  -> MikroTik RouterOS REST inventory / scoped revocation writes
  -> Fleet API inventory / invite secret / labels / targeted MDM commands
  -> Apple devices receive per-user IKEv2 .mobileconfig through MDM
```

## Operating Mode

The deployment remains conservative by default:

- `Deployment/vpn-identity-controller`: `replicas: 0`
- `CronJob/vpn-identity-controller-sync`: `suspend: true`
- `DRY_RUN=true`

In dry-run mode it imports existing MikroTik certificates such as
`re8ch-ikev2-client-harry-v2`, reads Fleet iOS hosts, computes invite links,
labels, device mapping, and MDM profile commands, and writes a redacted
`/data/state.json`.

When `DRY_RUN=false`, this controller may:

- add or remove controller-managed Fleet enroll secrets
- create/update RE8CH manual labels in Fleet
- update Fleet human-device mapping for matched serials
- send targeted Fleet MDM `InstallProfile` / `RemoveProfile` commands
- disable only RE8CH-prefixed MikroTik certificates/IPsec identities for users
  marked `absent`, `disabled`, or `revoked`

It does not patch Fleet internals and it does not invent iOS software, report,
policy, disk, or IP telemetry. Manually enrolled iOS devices do not run fleetd
or osquery, so those Fleet columns are expected to be sparse.

## Required Secrets

Create the secret out of band. Do not commit real values.

```bash
kubectl -n ops create secret generic vpn-identity-controller-secrets \
  --from-literal=mikrotik_base_url='https://192.0.2.131' \
  --from-literal=mikrotik_username='REPLACE_ME' \
  --from-literal=mikrotik_password='REPLACE_ME' \
  --from-literal=fleet_api_token='REPLACE_ME' \
  --from-literal=invite_signing_key='REPLACE_WITH_LONG_RANDOM_VALUE' \
  --dry-run=client -o yaml | kubectl apply -f -
```

`fleet_api_token` is optional for MikroTik-only inventory. Keep the RouterOS
account scoped to certificate and IPsec identity work once write mode exists.
`invite_signing_key` is required to create deterministic one-time Fleet enroll
secrets without storing those secrets in Git, logs, or state.

## Desired Users

Replace the empty `vpn-identity-controller-config` with the desired inventory:

```bash
kubectl -n ops create configmap vpn-identity-controller-config \
  --from-file=identities.json=config/vpn-identity/identities.example.json \
  --dry-run=client -o yaml | kubectl apply -f -
```

Each user record should include:

- `name`, `email`, and the expected iPhone `serial`
- `state`: `present`, `disabled`, or `absent`
- `vpn_state`: `present`, `disabled`, `revoked`, or `absent`
- `invite_token` and `invite_expires_at` for the one-time invite URL
- `certificate_name` matching the MikroTik RE8CH client certificate
- `vpn_profile_path` pointing to a mounted per-user `.mobileconfig` when the
  controller should send a targeted VPN install command

Do not commit P12 files, signed profiles, or real profile contents. Mount
per-user VPN profile material from a Kubernetes Secret or another approved
secret delivery path.

## Build

Build outside the cluster:

```bash
docker buildx build --platform linux/amd64 \
  -f apps/vpn-identity-controller/Dockerfile \
  -t registry.re8ch.com/ops/vpn-identity-controller:20260715-001 \
  --push .
```

## Deploy

```bash
kubectl apply -f manifests/k3s/ops/vpn-identity-controller/vpn-identity-controller.yaml
```

Run one suspended sync manually:

```bash
kubectl -n ops create job --from=cronjob/vpn-identity-controller-sync vpn-identity-controller-sync-manual
kubectl -n ops logs -f job/vpn-identity-controller-sync-manual
```

Expose the state viewer only inside the cluster:

```bash
kubectl -n ops scale deploy/vpn-identity-controller --replicas=1
kubectl -n ops port-forward svc/vpn-identity-controller 8080:8080
curl -fsS http://127.0.0.1:8080/state.json
```

The public invite route is exposed at:

```text
https://fleet.re8ch.com/re8ch-vpn/invite?user=<name>&token=<invite_token>
```

This route shows only the matched email, expected serial, and Fleet OTA
enrollment profile link. The internal `/state.json` endpoint is not exposed by
the public Ingress.

## Fleet Free Limits

Fleet Free can deliver global Apple configuration profiles and custom MDM
commands, but it cannot safely scope per-user Apple configuration profiles from
Controls without Premium label/fleet scoping. For RE8CH, per-user VPN profiles
must therefore be sent through `POST /api/v1/fleet/commands/run` to the matched
`host.uuid`.

Fleet Hosts can truthfully show the iOS host, MDM state, manual labels, device
mapping, and OS settings status. Fleet Software, Reports, and Policies are
osquery/fleetd-driven surfaces and are not reliable visibility channels for
manually enrolled iOS.

## RouterOS Remote Space

Enable RouterOS REST only on a management interface reachable from K3S, ideally
restricted to the `rancher-panel` or ops node source address. Do not expose it
to the public internet.

The controller expects RouterOS v7 REST paths:

- `/rest/certificate`
- `/rest/ip/ipsec/identity`

Future write mode should use a dedicated RouterOS user that can only manage:

- `/certificate`
- `/ip/ipsec/identity`
- the RE8CH IKEv2 peer/profile objects needed for employee VPN

## Certificate Lifecycle

The controller can disable RE8CH-prefixed MikroTik certificates and IPsec
identities during revoke/remove flows. Creating new RouterOS certificates and
exporting P12 material remains intentionally separate until the exact RouterOS
certificate command profile is validated in a staging run. Per-user VPN profile
bytes should be mounted into the controller and referenced by `vpn_profile_path`.
