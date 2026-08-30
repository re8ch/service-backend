# restricted-node-6dc3f140f9 PostgreSQL non-blocking boot

restricted-node-6dc3f140f9 remains the authoritative `pg-re8ch` primary. Its PostgreSQL data lives on
the Ceph RBD image `r640-rbd/pigsty-r640`, mounted at `/data/pigsty`.

The data plane is optional to Linux boot. `rbdmap`, Patroni and PgBouncer are
not enabled directly in `multi-user.target`. A timer starts the stack two
minutes after boot and bounds every storage and readiness wait. Failure leaves
the host reachable for repair instead of blocking system startup indefinitely.

Install from a reviewed checkout on restricted-node-6dc3f140f9:

```sh
sudo ./install.sh
```

The installer changes the next-boot policy only. It does not stop or replace a
currently running Patroni or PgBouncer process. Verify a future boot with:

```sh
systemctl status re8ch-postgres-stack.timer re8ch-postgres-stack.service
patronictl -c /etc/patroni/patroni.yml list
```
