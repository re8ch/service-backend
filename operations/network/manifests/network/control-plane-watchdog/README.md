# B1 Windows host recovery gate

`b1-windows.json` is the reviewed, nonsecret configuration installed at
`C:\ProgramData\RE8CH\control-plane-watchdog.json`. The task runs as SYSTEM
every five minutes and reads the WSL heartbeat written by
`re8ch-control-plane-heartbeat.timer`. A heartbeat is published when B1's
k3s and SSH services are active; the host accepts it for 180 seconds.
ZeroTier readiness is included as a diagnostic field but is not a host reboot
gate: loss of a management overlay must not reboot a functioning control plane.
This also avoids calling Administrator-owned WSL from SYSTEM.

After three unhealthy checks, host recovery requires both independent
signals: A1 Windows SSH on the `192.0.2.180/24` LAN and HTTPS at the
RouterOS-owned public Gateway. Neither endpoint is restricted-node-6dc3f140f9's K3s API. These
signals were tested from B1 Windows on 2026-10-03. They fence a host reboot;
they do not claim that two etcd members are healthy. WSL lifecycle recovery
is requested first, then the host watchdog waits another 600 + 1200 seconds
before rebooting. A six-hour host reboot cooldown remains in force.

Install the JSON and PowerShell script from
`operations/network/scripts/control-plane-watchdog/` together. Preserve the
previous files before replacing them. Verify the heartbeat is fresh, run the
scheduled task once, and require a `status=healthy` line plus a reset
`consecutiveFailures` counter. If either remote signal stops responding,
the task holds host reboot rather than claiming peer quorum.
