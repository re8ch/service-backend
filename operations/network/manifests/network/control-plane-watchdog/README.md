# A1 active-server and B1 worker-standby recovery gates

`a1-windows.json` and `b1-windows.json` are the reviewed, nonsecret
configurations installed at
`C:\ProgramData\RE8CH\control-plane-watchdog.json`. The task runs as SYSTEM
every five minutes and reads the WSL heartbeat written by
`re8ch-control-plane-heartbeat.timer`. A1 publishes health for `k3s.service`;
B1 publishes health for `k3s-agent.service`. The environment file binds each
installation to its reviewed node name, role, service and ZeroTier identity.
The host accepts a matching heartbeat for 180 seconds.
ZeroTier readiness is included as a diagnostic field but is not a host reboot
gate: loss of a management overlay must not reboot a functioning control plane.
This also avoids calling Administrator-owned WSL from SYSTEM.

After three unhealthy checks, WSL lifecycle recovery is requested first. The
watchdog waits the role-specific stagger before rebooting and retains the
six-hour host reboot cooldown. No peer is treated as quorum evidence: A1 is
the sole etcd member, and B1 is a worker whose watchdog is not authorized to
start or promote a K3s server.

Install the JSON and PowerShell script from
`operations/network/scripts/control-plane-watchdog/` together. Preserve the
previous files before replacing them. Verify the heartbeat is fresh, run the
scheduled task once, and require a `status=healthy` line plus a reset
`consecutiveFailures` counter. If either remote signal stops responding,
the task holds host reboot rather than claiming peer quorum.
