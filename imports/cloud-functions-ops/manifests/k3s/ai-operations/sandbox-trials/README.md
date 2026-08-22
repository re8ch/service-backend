# restricted-node-6dc3f140f9 self-hosted sandbox trials

> **Status (2026-08-21): CubeSandbox is deprecated.** Do not repair, upgrade,
> extend, or select `cube-trial` for new workloads. Preserve its PVCs and
> existing resources only until an explicit removal/migration decision. MitOS
> is the active microVM candidate; it is not production-ready until guest
> networking, tenant isolation, credential injection, terminal access, and
> fork/restore acceptance tests pass.

The trials compare CubeSandbox and MitOS on the existing K3s, Cilium and CephFS
infrastructure. They run in isolated namespaces and do not replace OpenSandbox.

Hard safety rules:

- no host-kernel installation or automatic reboot;
- no automatic CoreDNS rewrite;
- compute is pinned to `restricted-node-6dc3f140f9`;
- no public ingress during qualification;
- all durable test data uses dedicated CephFS claims;
- remove trial workloads without deleting shared CRDs or unrelated storage.

MitOS is pinned to chart/application `v1.43.0`. CubeSandbox is pinned to `v0.6.0`.

`cube-values.yaml` intentionally omits database passwords. Supply
`mysql.password`, `mysql.rootPassword`, and `redis.password` from a temporary
values file or Helm `--set-string` arguments. Never commit those values.

## 2026-08-17 restricted-node-6dc3f140f9 qualification result

Both runtimes reached real guest command execution on restricted-node-6dc3f140f9; this was not a
Pod-readiness-only test.

- CubeSandbox `v0.6.0`: the control plane, node runtime, CubeProxy, MySQL and
  Redis are running in `cube-trial`. The three stateful control-plane claims
  are bound through `cephfs-re8ch`. Template
  `tpl-0a3c5cfb6f2847b881b24668` built successfully from the upstream
  `sandbox-code:latest` image. Sandbox `93c2f34f26da40738ac44cd91dc18f85`
  executed a shell command, wrote `/home/cube-proof`, paused in about 1.09 s,
  resumed/connect-ed in about 32 ms, and retained the file.
- MitOS `v1.43.0`: pool `terminal-trial` is Ready and advertises the restricted-node-6dc3f140f9 KVM
  capacity. `terminal-session-2` executed commands through the token-gated
  sandbox API. Ubuntu 26.04 requires the documented forkd AppArmor/seccomp
  exceptions plus `FOWNER`; those settings are captured in `mitos-values.yaml`.

Cube guest egress was repaired on 2026-08-17 by enabling route-aware
`cube-router` and correcting the published chart/image variable mismatches
documented below. A fresh guest resolved through cluster DNS, reached GitHub
directly with HTTP 200, and reported public IP `192.0.2.189` through the
normal restricted-node-6dc3f140f9 -> MikroTik -> LA route. Direct OpenAI was reset by the mainland
cross-border path, while the same guest explicitly using the node-local Mihomo
Service reached OpenAI and received the expected unauthenticated HTTP 401.
Only CoreDNS `192.0.2.170/32` and Mihomo `192.0.2.252/32` were admitted from
Cube's otherwise private-range-denied policy.

MitOS still has `eth0` (`192.0.2.232/30`) but cannot reach its `192.0.2.108`
gateway. CephFS currently owns Cube control-plane durable state; it is not yet
mounted as a guest workspace volume.

Current selection (superseded on 2026-08-21): CubeSandbox is deprecated and
must receive no further repair or feature work. MitOS is now the active microVM
candidate. The next integration slice is deliberately narrow:
Cilium-compatible guest routing first, then a dedicated CephFS-backed workspace
mount, authenticated terminal access, tenant-scoped credential injection, and
fork/restore tests. No public endpoint may be enabled before those checks pass.

## Shared browser runtime

The first Steel Browser runtime is deployed in `ai-operations`, pinned to
`restricted-node-6dc3f140f9`. Chromium runs only inside Steel. Browser-Use connects directly to
`ws://steel-browser.ai-operations.svc.cluster.local:3000`; humans use the
Dex-authenticated Steel UI/Viewer at `https://browser.re8ch.com`.

Steel browser data uses the `steel-browser-data` CephFS claim. Cilium policies
allow API/CDP traffic from the Agent namespaces, DNS, browser web egress and the
optional `egress-fabric/mihomo-node:7890` path. Proxy selection is Steel runtime
or session configuration; there is no intermediary Broker.

The qualified open-source Steel image manages one active browser per API
process and currently normalizes persistent sessions to a fixed
`/app/api/user-data-dir`. The deployed runtime is therefore a single-profile
qualification pool. Multi-profile isolation is one Steel runtime per profile,
each with a fenced CephFS subpath. A runtime never switches profiles; the Agent
connects to a different runtime when it needs a different identity.

## CubeSandbox route-aware egress direction

Human operators use the Dex-protected public dashboard at
<https://cube.re8ch.com>. The same-origin `/cubeapi/v1` and `/sandbox/` paths
remain behind the same oauth2-proxy boundary; CubeAPI is not published as a
separate unauthenticated endpoint. The Gateway API resources are in
`cube-public-gateway.yaml`, and `register-cube-dex-client.sh` creates the
cluster-only OAuth credentials without committing them to Git.

Ordinary Sandbox detail pages include an **Open terminal** action supplied by
the `cube-webui-terminal/` overlay. It opens an xterm.js browser terminal backed
by envd PTY port `49983`; create/input/output/resize/kill were verified against
a disposable CubeBox. Leaving the page or selecting **End session** terminates
that PTY shell but does not kill the Sandbox.

CubeOps v0.6.0 cannot connect an existing Sandbox against the deployed
CubeMaster because it calls a removed internal route. The terminal overlay has
a narrowly scoped compatibility route: nginx first validates the browser JWT
through CubeOps, then calls CubeAPI's supported connect endpoint with the
server-mounted admin token. Sandbox detail responses alone are insufficient
because they do not contain the envd access token. Operational upgrade,
acceptance and rollback steps are in `cube-webui-terminal/README.md`.

This is an infrastructure integration trial and does not publish custom
runtime images. Keep the upstream v0.6.0 images. The runtime supports
`cube-router`, but the v0.6.0 Kubernetes chart does not expose its settings as
native values. Apply the checked-in patch after every Helm install or upgrade:

```bash
kubectl -n cube-trial patch daemonset cube-trial-node --type strategic \
  --patch-file cube-router-daemonset.patch.yaml
kubectl -n cube-trial rollout status daemonset/cube-trial-node
```

The patch supplies `CUBE_SANDBOX_CUBE_ROUTER_ENABLE=1` to both
`network-agent` and `cubelet`. The published v0.6.0 component image does not
consume that variable even though the current v0.6.0 source does. The cubelet
wrapper therefore patches the packaged TOML before the upstream entrypoint
stages it. The network-agent wrapper waits for that staging, verifies and
repairs the route-aware fields, then execs its upstream entrypoint. Both
containers clear the published entrypoint's CIDR override to avoid its
unanchored replacement rewriting `cube_router_cidr`; the desired sandbox CIDR
is written directly to the packaged TOML. This remains restart-safe and uses
only upstream images.

Cubelet moves itself into its dedicated host cgroup during startup and may
hand off to a different PID. The published entrypoint can capture the old PID
and exit while the real cubelet keeps serving. The wrapper re-discovers the
serving PID after that exit, verifies port 9999, updates the pidfile, and owns
its termination signal so DaemonSet updates do not leave an orphan cubelet.

The patch also supplies `CUBE_SANDBOX_NETWORK_CIDR=192.0.2.33/18` to
`cube-egress-net`. Its published iptables script derives the TPROXY listener
address from this variable and does not consume the chart's
`CUBE_TPROXY_ON_IP`, despite that value being present in the DaemonSet.

Guest traffic then enters the cube-node Pod's Linux routing stack and follows
the restricted-node-6dc3f140f9/Cilium path; restricted-node-6dc3f140f9's upstream default route is managed at the MikroTik
boundary. No additional eBPF program is added to a CubeBox.

Mihomo remains an explicit optional path. A sandbox process that needs it may
set:

```bash
export HTTP_PROXY=http://192.0.2.252:7890
export HTTPS_PROXY=http://192.0.2.252:7890
export NO_PROXY=127.0.0.1,localhost,.svc,.cluster.local
```

`192.0.2.252:7890` is the `egress-fabric/mihomo-node` Service with local
traffic policy. Do not set these variables globally: direct traffic should
inherit the normal restricted-node-6dc3f140f9 -> MikroTik route, while Mihomo is selected only for
destinations that need an explicit cross-border proxy.

Cube's default network policy denies private ranges. Sandboxes that opt into
Mihomo must allow only the DNS and proxy endpoints they consume:

```json
{
  "network": {
    "allowOut": ["192.0.2.170/32", "192.0.2.252/32"]
  }
}
```

Do not allow the full Service CIDR or `192.0.2.183/8` just to reach the proxy.
