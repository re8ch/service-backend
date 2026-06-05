#!/usr/bin/env bash
set -euo pipefail

ICP_HOST="${ICP_HOST:-ICP}"
REALTIME_IMAGE="${REALTIME_IMAGE:-supabase/realtime:v2.102.3}"
REALTIME_PORT="${REALTIME_PORT:-4000}"
TENANT_ID="${TENANT_ID:-api}"
K3S_NAMESPACE="${K3S_NAMESPACE:-qwen}"
QWEN_ENV_SECRET="${QWEN_ENV_SECRET:-qianwen-prod-env-full}"
QWEN_API_DEPLOYMENT="${QWEN_API_DEPLOYMENT:-nuclio-qianwen-prod-api-core}"

ssh "$ICP_HOST" "REALTIME_IMAGE='$REALTIME_IMAGE' REALTIME_PORT='$REALTIME_PORT' TENANT_ID='$TENANT_ID' K3S_NAMESPACE='$K3S_NAMESPACE' QWEN_ENV_SECRET='$QWEN_ENV_SECRET' QWEN_API_DEPLOYMENT='$QWEN_API_DEPLOYMENT' bash -s" <<'REMOTE'
set -euo pipefail

KEY="$HOME/.ssh/pigsty_admin"
PRIMARY="ubuntu@192.0.2.172"
NODES=("ubuntu@192.0.2.172" "admin@192.0.2.141" "root@192.0.2.33")

ssh_pg() {
  local spec="$1"
  shift
  ssh -i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$spec" "$@"
}

secret_from_k8s() {
  local key="$1"
  if sudo k3s kubectl -n "$K3S_NAMESPACE" get secret "$QWEN_ENV_SECRET" >/dev/null 2>&1; then
    sudo k3s kubectl -n "$K3S_NAMESPACE" get secret "$QWEN_ENV_SECRET" -o "jsonpath={.data.$key}" | base64 -d
    return
  fi
  sudo k3s kubectl -n "$K3S_NAMESPACE" get deploy "$QWEN_API_DEPLOYMENT" -o json \
    | python3 -c 'import json,sys; key=sys.argv[1]; d=json.load(sys.stdin); env=d["spec"]["template"]["spec"]["containers"][0].get("env", []); print(next((x.get("value","") for x in env if x.get("name")==key), ""))' "$key"
}

JWT_SECRET="$(secret_from_k8s PGRST_JWT_SECRET)"
if [[ -z "$JWT_SECRET" ]]; then
  echo "PGRST_JWT_SECRET missing from $K3S_NAMESPACE/$QWEN_ENV_SECRET" >&2
  exit 1
fi

STATE_DIR="$HOME/.qwen-realtime"
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"
if [[ ! -f "$STATE_DIR/db-password" ]]; then
  openssl rand -base64 36 > "$STATE_DIR/db-password"
  chmod 600 "$STATE_DIR/db-password"
fi
if [[ ! -f "$STATE_DIR/db-enc-key" ]] || [[ "$(wc -c < "$STATE_DIR/db-enc-key" | tr -d ' ')" -ne 17 ]]; then
  openssl rand -hex 8 > "$STATE_DIR/db-enc-key"
  chmod 600 "$STATE_DIR/db-enc-key"
fi
if [[ ! -f "$STATE_DIR/secret-key-base" ]]; then
  openssl rand -base64 64 > "$STATE_DIR/secret-key-base"
  chmod 600 "$STATE_DIR/secret-key-base"
fi

DB_PASSWORD="$(cat "$STATE_DIR/db-password")"
DB_ENC_KEY="$(cat "$STATE_DIR/db-enc-key")"
SECRET_KEY_BASE="$(cat "$STATE_DIR/secret-key-base")"

ssh_pg "$PRIMARY" "sudo -iu postgres psql -X -v ON_ERROR_STOP=1 -v qwen_realtime_password='${DB_PASSWORD//\'/\'\\\'\'}' -d qianwen_prod" <<'SQL'
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'qwen_realtime') then
    create role qwen_realtime login replication;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin bypassrls;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'supabase_realtime_admin') then
    create role supabase_realtime_admin noinherit nologin noreplication;
  end if;
end
$$;
alter role qwen_realtime with login replication password :'qwen_realtime_password';
grant dbrole_readonly to qwen_realtime;
grant set on parameter log_min_messages to qwen_realtime;
grant supabase_realtime_admin to qwen_realtime with admin option;
grant anon, authenticated, service_role to qwen_realtime;
grant web_user to qwen_realtime;
grant connect, create on database qianwen_prod to qwen_realtime;
create schema if not exists _realtime authorization qwen_realtime;
create schema if not exists realtime authorization qwen_realtime;
alter role qwen_realtime in database qianwen_prod set search_path to _realtime;
grant usage, create on schema realtime to supabase_realtime_admin;
grant usage on schema api to qwen_realtime;
grant select on api.task_event_outbox to qwen_realtime;
grant select on api.task_event_outbox_event_id_seq to qwen_realtime;
grant usage on schema api to authenticated;
grant usage on schema realtime to anon, authenticated, service_role;
grant select on api.task_event_outbox to authenticated;
grant usage, select on sequence api.task_event_outbox_event_id_seq to authenticated;
update _realtime.extensions
set settings = jsonb_set(
  jsonb_set(settings, '{publication}', '"qwen_task_events_pub"'::jsonb),
  '{slot_name}', '"qwen_task_events_slot"'::jsonb
)
where tenant_external_id = 'api'
  and type = 'postgres_cdc_rls';
SQL

for spec in "${NODES[@]}"; do
  echo "installing qwen realtime on $spec"
  ssh_pg "$spec" "sudo install -d -m 0700 -o root -g root /opt/qwen-realtime"
  ssh_pg "$spec" "sudo tee /opt/qwen-realtime/qwen-realtime.env >/dev/null" <<EOF
PORT=${REALTIME_PORT}
DB_HOST=127.0.0.1
DB_PORT=5432
DB_USER=qwen_realtime
DB_PASSWORD=${DB_PASSWORD}
DB_NAME=qianwen_prod
DB_AFTER_CONNECT_QUERY=SET search_path TO _realtime
DB_ENC_KEY=${DB_ENC_KEY}
API_JWT_SECRET=${JWT_SECRET}
METRICS_JWT_SECRET=${JWT_SECRET}
SECRET_KEY_BASE=${SECRET_KEY_BASE}
ERL_AFLAGS=-proto_dist inet_tcp
DNS_NODES=''
RLIMIT_NOFILE=10000
APP_NAME=realtime
SEED_SELF_HOST=false
RUN_JANITOR=true
DISABLE_HEALTHCHECK_LOGGING=true
SLOT_NAME=qwen_task_events_slot
PUBLICATIONS=qwen_task_events_pub
EOF
  ssh_pg "$spec" "sudo chmod 0600 /opt/qwen-realtime/qwen-realtime.env"
  ssh_pg "$spec" "sudo docker rm -f qwen-realtime >/dev/null 2>&1 || true"

  ssh_pg "$spec" "sudo tee /usr/local/bin/qwen-realtime-role-sync >/dev/null" <<EOF
#!/usr/bin/env bash
set -euo pipefail
IMAGE="${REALTIME_IMAGE}"
PORT="${REALTIME_PORT}"
TENANT_ID="${TENANT_ID}"
LOCK_FILE="/run/qwen-realtime-role-sync.lock"
exec 9>"\$LOCK_FILE"
flock -n 9 || exit 0
stop_realtime() {
  if docker inspect qwen-realtime >/dev/null 2>&1; then
    docker stop qwen-realtime >/dev/null 2>&1 || true
  fi
}
if ! IN_RECOVERY="\$(sudo -iu postgres psql -X -qAt -d qianwen_prod -c 'select pg_is_in_recovery()')"; then
  stop_realtime
  exit 0
fi
if [[ "\$IN_RECOVERY" == "f" ]]; then
  if ! docker inspect qwen-realtime >/dev/null 2>&1; then
    docker pull "\$IMAGE" >/dev/null
    docker run -d \\
      --name qwen-realtime \\
      --restart unless-stopped \\
      --network host \\
      --memory 512m \\
      --cpus 1.0 \\
      --env-file /opt/qwen-realtime/qwen-realtime.env \\
      "\$IMAGE" >/dev/null
  elif [[ "\$(docker inspect -f '{{.State.Running}}' qwen-realtime 2>/dev/null || echo false)" != "true" ]]; then
    docker start qwen-realtime >/dev/null
  fi
else
  stop_realtime
fi
EOF
  ssh_pg "$spec" "sudo chmod 0755 /usr/local/bin/qwen-realtime-role-sync"

  ssh_pg "$spec" "sudo tee /usr/local/bin/qwen-realtime-patroni-callback >/dev/null" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
ACTION="${1:-unknown}"
ROLE="${2:-unknown}"
CLUSTER="${3:-unknown}"
LOG_FILE="/opt/qwen-realtime/patroni-callback.log"
{
  printf '%s action=%s role=%s cluster=%s\n' "$(date -Is)" "$ACTION" "$ROLE" "$CLUSTER"
} >> "$LOG_FILE" 2>/dev/null || true
systemctl --no-block start qwen-realtime-role-sync.service
EOF
  ssh_pg "$spec" "sudo chmod 0755 /usr/local/bin/qwen-realtime-patroni-callback"

  ssh_pg "$spec" "sudo tee /etc/systemd/system/qwen-realtime-role-sync.service >/dev/null" <<'EOF'
[Unit]
Description=Qwen Realtime primary role sync
After=postgresql.service patroni.service docker.service
Wants=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/qwen-realtime-role-sync
EOF

  ssh_pg "$spec" "sudo tee /etc/systemd/system/qwen-realtime-role-sync.timer >/dev/null" <<'EOF'
[Unit]
Description=Watchdog for Qwen Realtime primary role sync

[Timer]
OnBootSec=30s
OnUnitActiveSec=60s
AccuracySec=10s
Unit=qwen-realtime-role-sync.service

[Install]
WantedBy=timers.target
EOF

  ssh_pg "$spec" "sudo tee /opt/qwen-realtime/patch-patroni-callbacks.py >/dev/null" <<'EOF'
#!/usr/bin/env python3
import os
import pathlib
import shutil
import time
import yaml

path = pathlib.Path('/etc/patroni/patroni.yml')
callback = '/usr/local/bin/qwen-realtime-patroni-callback'
events = ['on_role_change', 'on_start', 'on_restart', 'on_stop']
data = yaml.safe_load(path.read_text()) or {}
postgresql = data.setdefault('postgresql', {})
callbacks = postgresql.setdefault('callbacks', {})
changed = False
for event in events:
    if callbacks.get(event) != callback:
        callbacks[event] = callback
        changed = True
if changed:
    stat = path.stat()
    backup = path.with_name(f"{path.name}.qwen-realtime.{time.strftime('%Y%m%d%H%M%S')}.bak")
    shutil.copy2(path, backup)
    tmp = path.with_suffix(path.suffix + '.qwen-realtime.tmp')
    tmp.write_text(yaml.safe_dump(data, default_flow_style=False, sort_keys=False))
    os.chown(tmp, stat.st_uid, stat.st_gid)
    os.chmod(tmp, stat.st_mode & 0o777)
    tmp.replace(path)
    print(f"changed backup={backup}")
else:
    print("ok")
EOF
  ssh_pg "$spec" "sudo chmod 0755 /opt/qwen-realtime/patch-patroni-callbacks.py"
  ssh_pg "$spec" "sudo systemctl daemon-reload && sudo systemctl enable --now qwen-realtime-role-sync.timer && sudo /opt/qwen-realtime/patch-patroni-callbacks.py && sudo systemctl reload patroni && sudo systemctl start qwen-realtime-role-sync.service"
done

for spec in "${NODES[@]}"; do
  echo "== $spec =="
  ssh_pg "$spec" "in_recovery=\$(sudo -iu postgres psql -X -qAt -d qianwen_prod -c 'select pg_is_in_recovery()'); running=\$(sudo docker inspect -f '{{.State.Running}}' qwen-realtime 2>/dev/null || echo absent); echo in_recovery=\$in_recovery running=\$running; sudo docker ps --filter name=qwen-realtime --format '{{.Names}} {{.Status}}' || true"
done
REMOTE
