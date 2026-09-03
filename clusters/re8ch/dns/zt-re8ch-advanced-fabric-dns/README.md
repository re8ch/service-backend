# re8ch ZeroTier static authority

Three API-independent Advanced Fabric DNS instances authoritatively serve
`zt.re8ch.com` on `10.181.22.73`, `10.181.22.16`, and `10.181.22.95`.
They are an optional management-plane bootstrap aid, never a prerequisite for
Kubernetes `cluster.local` DNS or ZeroTier Planet/Moon discovery.

Install the v0.11.4 `dns_server.py`, this unit and `records.json`, set the local
`BIND_ADDRESS`, then replace one server at a time. Before advancing, query the
changed server over UDP and TCP from each of the two unchanged servers.
