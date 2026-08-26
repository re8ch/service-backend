# Personal public access edge

`access re8ch com` exposes the existing Mihomos Cluster to devices that are not
members of ZeroTier/NetBird. RouterOS remains a public L4 entry point only:

- TCP/443 is forwarded to the Cilium Gateway VIP `192.0.2.213`; TLS SNI selects
  Auto, US California, Malaysia Kuala Lumpur, or the Dex subscription portal.
- UDP/443 is forwarded to `192.0.2.224`; Hysteria2 uses one multi-endpoint
  service because UDP cannot be shared using TLS SNI.
- The regional Mihomo listeners hand traffic to the node-local smart-egress
  SOCKS listener on `127.0.0.1:17891`.
- Subscription URLs and proxy credentials are unique per device. Revocation or
  rotation updates the runtime Secret and rolls both regional Deployments.

## Bootstrap

Run these only after the GitLab change is merged and before reconciling Flux:

```sh
clusters/re8ch/components/personal-access/bootstrap-runtime-secrets.sh
clusters/re8ch/components/personal-access/register-dex-client.sh
```

The scripts generate values in temporary files with mode `0700`, do not print
them, and never write them into Git. The Dex script creates a timestamped backup
Secret and reports its name.

## Qualification and publication

The production names publish RouterOS `192.0.2.138` with TTL 60. Keep a
regional node in the subscription only after it sustains at least 100 Mbps in a
four-stream qualification test. The Kuala Lumpur listener remains an
unpublished candidate because it did not meet that gate.

The release checks are:

1. The published AnyTLS SNI presents a valid certificate and its regional node
   sustains at least 100 Mbps.
2. UDP/443 completes a Hysteria2 transfer through `192.0.2.224`.
3. `sub.access.re8ch.com` completes Dex login and creates, imports, rotates and
   revokes a Shadowrocket subscription.
4. Existing HTTPS listeners sharing TCP/443 remain healthy.

The old public edge remains the rollback target until a full observation window
completes.

## Rollback

Restore the access DNS target to `192.0.2.192`, disable only the two RouterOS
DNAT rules tagged `re8ch-personal-access`, and restore the reported pre-change
Dex Secret if login is affected. The shared RouterOS TCP/443 listener and other
Gateway hostnames must not be removed.
