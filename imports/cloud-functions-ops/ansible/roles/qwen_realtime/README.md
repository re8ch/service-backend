# Qwen Realtime Pigsty Role

Deploys the lightweight Supabase Realtime service on Pigsty PostgreSQL nodes without
joining those database hosts to K3S.

The role is intentionally primary-active / replica-standby:

- every pg-meta node receives the same env file, role-sync script, callback, and
  watchdog timer
- Patroni callbacks trigger role sync on `on_role_change`, `on_start`,
  `on_restart`, and `on_stop`
- the local `pg_is_in_recovery()` result still decides whether the container
  should run; callback arguments are only logged
- only the current Patroni primary runs `qwen-realtime`
- replicas keep the watchdog timer enabled and stop the container while in recovery

## Required Inventory

Define a `qwen_realtime` host group containing the three Pigsty nodes. Supply secrets
from Ansible Vault or an external secret loader:

```yaml
qwen_realtime_db_password: "..."
qwen_realtime_api_jwt_secret: "..."
qwen_realtime_db_enc_key: "16-char-secret"
qwen_realtime_secret_key_base: "..."
```

Do not commit those values.

See `ansible/inventory.example.yml` for a non-secret inventory skeleton.

## Patroni Callback

By default the role patches `/etc/patroni/patroni.yml` and backs up the original
file before adding:

```yaml
postgresql:
  callbacks:
    on_role_change: /usr/local/bin/qwen-realtime-patroni-callback
    on_start: /usr/local/bin/qwen-realtime-patroni-callback
    on_restart: /usr/local/bin/qwen-realtime-patroni-callback
    on_stop: /usr/local/bin/qwen-realtime-patroni-callback
```

The callback returns quickly by starting `qwen-realtime-role-sync.service` with
`systemctl --no-block`. The timer defaults to 60 seconds and acts only as a
watchdog if a callback is missed.

If a Pigsty deployment keeps Patroni config in another location, override:

```yaml
qwen_realtime_patroni_config_path: /path/to/patroni.yml
```

If Pigsty should render Patroni config elsewhere and this role should not patch
it directly, set:

```yaml
qwen_realtime_manage_patroni_callbacks: false
```

## Run

```bash
ansible-playbook -i inventory.yml ansible/playbooks/qwen-realtime.yml
```

## Notes

- The Realtime tenant defaults to `api` so `api.jz.ynzs.com` maps to the correct tenant.
- The CDC source is limited to `qwen_task_events_pub`, which should only include
  `api.task_event_outbox`.
- The role does not expose a public WS origin. ESA/Ingress still needs to route
  `/realtime/v1` or an equivalent path to the healthy primary Realtime instance.
- This role deliberately avoids installing kubelet/CNI/iptables components on DB nodes.
- The Patroni config patcher needs `python3-yaml` on the database nodes.
