# Personal public access edge

`access re8ch com` exposes the existing Mihomos Cluster to devices that are not
members of ZeroTier/NetBird. RouterOS remains a public L4 entry point only:

- TCP/443 is forwarded to the Cilium Gateway VIP `192.0.2.209`; TLS SNI selects
  Auto, US California, Malaysia Kuala Lumpur, or the Dex subscription portal.
- UDP/443 is forwarded to `192.0.2.75`; Hysteria2 uses one multi-endpoint
  service because UDP cannot be shared using TLS SNI.
- The regional Mihomo listeners hand traffic to the node-local smart-egress
  SOCKS listener on `127.0.0.1:17891`.
- Subscription URLs and proxy credentials are unique per device. Revocation or
  rotation updates the runtime Secret and rolls both regional Deployments.

Publication and runtime data are deliberately independent. A catalog entry has
a public protocol, hostname, port, SNI and display name, plus a separate runtime
Service, listener, region, health and qualification result. The Store never
publishes an internal Service address, and the controller never derives a
listener from client-facing values.

## Membership and control plane

The private, otherwise empty `re8ch/access-edge-members` GitHub repository is
the membership authority. Accepted collaborators with `read`, `triage` or
`write` may use Access Edge; `maintain` and `admin` may also open `/admin`.
Pending invitations are not members. `Woomou` is canonicalized to `hliu74` so
the GitHub, Apple and Dex identities retain one account and administrator role.

The GitHub App must be installed only on that repository and granted only
Repository metadata read access. Install its generated values without printing
or committing them:

```sh
GITHUB_APP_ID=... \
GITHUB_INSTALLATION_ID=... \
GITHUB_PRIVATE_KEY_FILE=/secure/path/app.pem \
  clusters/re8ch/components/personal-access/install-github-app-secret.sh
```

`access-controller` refreshes collaborators every 60 seconds using ETags. It
uses the last successful snapshot during an outage, revokes removed members'
devices and runtime users, and fails closed everywhere after the snapshot is 24
hours old. Runtime membership, device and usage ConfigMaps are intentionally
not Flux-owned because their writes use Kubernetes `resourceVersion` CAS.

The Store exposes these authenticated interfaces:

- `/s/{token}`: compatible base64 node subscription.
- `/s/{token}/mihomo.yaml`: complete Mihomo configuration.
- `/s/{token}/shadowrocket.conf`: Shadowrocket routing configuration.
- `/r/{token}/current/{ruleset}`: authorized, ETag-enabled current rule file.
- `/api/me`, `/api/status`, `/api/admin/users`: account, health and admin state.

Device telemetry reads the loopback-only Mihomo controller and aggregates
`inboundUser` upload/download bytes, connection count and last-seen time. It
stores device IDs only—never queried or visited domains. The 300 GB allowance
is a monthly soft reminder and does not enforce billing or throttling.

## Rules and DNS

The daily rule synchronizer obtains Mihomo MRS artifacts from MetaCubeX and
Shadowrocket-native lists from blackmatrix7. Each atomic version records its
source URL, SHA-256 and size metric. Empty data and changes larger than 25%
against the previous version are rejected; seven valid versions are retained.

Mihomo uses fake-IP DNS. CN/private and domestic Apple/Microsoft answers use
AliDNS and DNSPod, while overseas DoH travels through the proxy. Direct and
proxy-server resolvers stay domestic to avoid recursive node lookup. The rules
run private direct, domestic direct, overseas service groups, CN domain/IP
direct and finally `MATCH,PROXY`; IP providers use `no-resolve`.

Access DNS is internal only at `192.0.2.78`: two MosDNS replicas provide split
resolution and a recoverable AdGuard Home instance supplies conservative
filtering plus a 24-hour query UI. TCP/UDP 53 is admitted only from the RouterOS
LAN and approved cluster network. The AdGuard UI is protected by Dex at
`dns.access.re8ch.com`; no public DNS listener is created. CoreDNS and
NodeLocal DNS are unchanged.

## Bootstrap

Run these only after the GitLab change is merged and before reconciling Flux:

```sh
clusters/re8ch/components/personal-access/bootstrap-runtime-secrets.sh
clusters/re8ch/components/personal-access/register-dex-client.sh
```

The scripts generate values in temporary files with mode `0700`, do not print
them, and never write them into Git. The Dex script creates a timestamped backup
Secret and reports its name.

After Access DNS is healthy, RouterOS may use `192.0.2.78` as its primary DNS
and domestic resolvers as fallback. RouterOS administration is allowed only by
SSH to `192.0.2.131`; do not enable or use a public management address.

## Qualification and publication

The production names publish RouterOS `192.0.2.79` with TTL 60. Keep a
regional node in the subscription only after it sustains at least 100 Mbps in a
four-stream qualification test. The Kuala Lumpur listener remains an
unpublished candidate because it did not meet that gate.

The release checks are:

1. The published AnyTLS SNI presents a valid certificate and its regional node
   sustains at least 100 Mbps.
2. UDP/443 completes a Hysteria2 transfer through `192.0.2.75`.
3. `sub.access.re8ch.com` completes Dex login and creates, imports, rotates and
   revokes a Shadowrocket subscription.
4. Existing HTTPS listeners sharing TCP/443 remain healthy.
5. Membership removal is reflected within five minutes, stale authorization
   fails closed at 24 hours, and cross-account device operations return 404.
6. Domestic lookups return domestic CDN answers, overseas lookups use proxy
   DoH, and TCP/UDP 53 remains unreachable from the public Internet.

The old public edge remains the rollback target until a full observation window
completes.

## Rollback

Point RouterOS back to its previous domestic DNS, disable only the two RouterOS
DNAT rules tagged `re8ch-personal-access`, and restore the reported pre-change
Dex Secret if login is affected. For rule rollback, atomically repoint the
`/rules/current` symlink to one of the retained versions. The shared RouterOS
TCP/443 listener and other Gateway hostnames must not be removed.
