# re8ch ZeroTier DNS

This directory records the private DNS setup for `zt.re8ch.com`.

Authoritative DNS servers:

| Name | ZT IP | Host |
| --- | --- | --- |
| `ns1.zt.re8ch.com` | `192.0.2.192` | ICP / legacy DNS and infra worker; not a K3s control-plane endpoint |
| `ns2.zt.re8ch.com` | `192.0.2.138` | Headlamp / metrics panel |
| `ns3.zt.re8ch.com` | `192.0.2.74` | Harbor registry |

ZeroTier Managed DNS should use:

```text
Domain: zt.re8ch.com
Servers:
192.0.2.192
192.0.2.138
192.0.2.74
```

CoreDNS listens only on each host's ZeroTier IP, not on `127.0.0.53`, so it does not replace `systemd-resolved`.

K3s pods resolve `zt.re8ch.com` through the Advanced Fabric DNS conditional
forwarder, which sends the private zone to these three authoritative servers.

`rancher.zt.re8ch.com` is kept as a legacy compatibility alias. Prefer `headlamp.zt.re8ch.com`, `panel.zt.re8ch.com`, and `metrics.zt.re8ch.com` for new configs.
