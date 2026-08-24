$ORIGIN zt.re8ch.com.
$TTL 60
@       IN SOA ns1.zt.re8ch.com. ops.re8ch.com. (
            2026060701 ; serial
            60         ; refresh
            30         ; retry
            604800     ; expire
            60         ; minimum
        )

        IN NS ns1.zt.re8ch.com.
        IN NS ns2.zt.re8ch.com.
        IN NS ns3.zt.re8ch.com.

; Distributed private DNS servers.
ns1             IN A 192.0.2.192
ns2             IN A 192.0.2.138
ns3             IN A 192.0.2.74
dns             IN A 192.0.2.192
dns             IN A 192.0.2.138
dns             IN A 192.0.2.74

; Base host records.
@               IN A 192.0.2.192
pg              IN A 192.0.2.89
nuc             IN A 192.0.2.182
pg1             IN A 192.0.2.112
nuc2            IN A 192.0.2.154
pg2             IN A 192.0.2.251
headlamp        IN A 192.0.2.138
harbor          IN A 192.0.2.74

; ICP / infra aliases.
planet          IN CNAME zt.re8ch.com.
icp             IN CNAME zt.re8ch.com.
infra           IN CNAME zt.re8ch.com.
pigsty          IN CNAME zt.re8ch.com.
grafana         IN CNAME zt.re8ch.com.
k3s             IN CNAME zt.re8ch.com.
k8s             IN CNAME zt.re8ch.com.
otel            IN CNAME zt.re8ch.com.
collector       IN CNAME zt.re8ch.com.

; PostgreSQL primary aliases.
pgmain          IN CNAME pg.zt.re8ch.com.
pgwrite         IN CNAME pg.zt.re8ch.com.
postgres        IN CNAME pg.zt.re8ch.com.
pgsql           IN CNAME pg.zt.re8ch.com.
pgadmin         IN CNAME pg.zt.re8ch.com.
pgrst           IN CNAME pg.zt.re8ch.com.
pgrst-write     IN CNAME pg.zt.re8ch.com.
postgrest       IN CNAME pg.zt.re8ch.com.

; PostgreSQL Shanghai replica aliases.
pgrep-sh        IN CNAME pg1.zt.re8ch.com.
pgread-sh       IN CNAME pg1.zt.re8ch.com.
pgrst-sh        IN CNAME pg1.zt.re8ch.com.
pgrst-read-sh   IN CNAME pg1.zt.re8ch.com.

; PostgreSQL Guangzhou replica aliases.
pgrep-gz        IN CNAME pg2.zt.re8ch.com.
pgread-gz       IN CNAME pg2.zt.re8ch.com.
pgrst-gz        IN CNAME pg2.zt.re8ch.com.
pgrst-read-gz   IN CNAME pg2.zt.re8ch.com.

; Nuclio / observability aliases.
nuclio          IN CNAME nuc.zt.re8ch.com.
nuclio-panel    IN CNAME nuc.zt.re8ch.com.
nuclio-agent    IN CNAME nuc.zt.re8ch.com.
obs             IN CNAME nuc.zt.re8ch.com.
tempo           IN CNAME nuc.zt.re8ch.com.
prometheus      IN CNAME nuc.zt.re8ch.com.
nuc2-light      IN CNAME nuc2.zt.re8ch.com.

; Headlamp / management-plane aliases.
panel           IN CNAME headlamp.zt.re8ch.com.
headlamp-panel  IN CNAME headlamp.zt.re8ch.com.
metrics         IN CNAME headlamp.zt.re8ch.com.
grafana-panel   IN CNAME headlamp.zt.re8ch.com.
dbgui           IN CNAME headlamp.zt.re8ch.com.
ops             IN CNAME headlamp.zt.re8ch.com.

; Legacy compatibility aliases. Prefer headlamp.zt.re8ch.com for new configs.
rancher         IN CNAME headlamp.zt.re8ch.com.
rancher-ui      IN CNAME headlamp.zt.re8ch.com.

; Registry aliases.
registry        IN CNAME harbor.zt.re8ch.com.
