#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-supabase-pg-re8ch}"
SECRET_NAME="${SECRET_NAME:-supabase-pg-re8ch-secrets}"
POSTGRES_HOST="${POSTGRES_HOST:-192.0.2.178}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_DB="${POSTGRES_DB:-postgres}"
PUBLIC_URL="${PUBLIC_URL:-http://supabase-pg-re8ch.supabase-pg-re8ch.svc.cluster.local:8000}"
DASHBOARD_USERNAME="${DASHBOARD_USERNAME:-supabase}"
DASHBOARD_PASSWORD="${DASHBOARD_PASSWORD:-$(openssl rand -base64 30 | tr -d '\n')}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(openssl rand -base64 36 | tr -d '\n')}"
JWT_SECRET="${JWT_SECRET:-$(openssl rand -base64 48 | tr -d '\n')}"
PG_META_CRYPTO_KEY="${PG_META_CRYPTO_KEY:-$(openssl rand -hex 16)}"
SECRET_KEY_BASE="${SECRET_KEY_BASE:-$(openssl rand -base64 64 | tr -d '\n')}"
REALTIME_DB_ENC_KEY="${REALTIME_DB_ENC_KEY:-$(openssl rand -hex 8)}"

make_jwt() {
  local role="$1"
  JWT_ROLE="$role" JWT_SECRET="$JWT_SECRET" python3 - <<'PY'
import base64, hashlib, hmac, json, os, time

def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

header = {"alg": "HS256", "typ": "JWT"}
now = int(time.time())
payload = {
    "iss": "supabase",
    "ref": "pg-re8ch",
    "role": os.environ["JWT_ROLE"],
    "iat": now,
    "exp": now + 315360000,
}
msg = f"{b64url(json.dumps(header,separators=(',',':')).encode())}.{b64url(json.dumps(payload,separators=(',',':')).encode())}"
sig = hmac.new(os.environ["JWT_SECRET"].encode(), msg.encode(), hashlib.sha256).digest()
print(f"{msg}.{b64url(sig)}")
PY
}

ANON_KEY="${ANON_KEY:-$(make_jwt anon)}"
SERVICE_ROLE_KEY="${SERVICE_ROLE_KEY:-$(make_jwt service_role)}"
POSTGRES_PASSWORD_URLENCODED="$(POSTGRES_PASSWORD="$POSTGRES_PASSWORD" python3 - <<'PY'
import os, urllib.parse
print(urllib.parse.quote(os.environ["POSTGRES_PASSWORD"], safe=""))
PY
)"

kubectl get namespace "$NAMESPACE" >/dev/null 2>&1 || kubectl create namespace "$NAMESPACE"

kubectl -n "$NAMESPACE" create secret generic "$SECRET_NAME" \
  --dry-run=client -o yaml \
  --from-literal=POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  --from-literal=POSTGRES_PASSWORD_URLENCODED="$POSTGRES_PASSWORD_URLENCODED" \
  --from-literal=POSTGRES_USER_READ_WRITE="supabase_admin" \
  --from-literal=JWT_SECRET="$JWT_SECRET" \
  --from-literal=ANON_KEY="$ANON_KEY" \
  --from-literal=SERVICE_ROLE_KEY="$SERVICE_ROLE_KEY" \
  --from-literal=DASHBOARD_USERNAME="$DASHBOARD_USERNAME" \
  --from-literal=DASHBOARD_PASSWORD="$DASHBOARD_PASSWORD" \
  --from-literal=PG_META_CRYPTO_KEY="$PG_META_CRYPTO_KEY" \
  --from-literal=SECRET_KEY_BASE="$SECRET_KEY_BASE" \
  --from-literal=REALTIME_DB_ENC_KEY="$REALTIME_DB_ENC_KEY" \
  --from-literal=GOTRUE_DB_DATABASE_URL="postgres://supabase_auth_admin:${POSTGRES_PASSWORD_URLENCODED}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" \
  --from-literal=PGRST_DB_URI="postgres://authenticator:${POSTGRES_PASSWORD_URLENCODED}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" \
  --from-literal=STORAGE_DATABASE_URL="postgres://supabase_storage_admin:${POSTGRES_PASSWORD_URLENCODED}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" \
  | kubectl apply -f -

cat <<EOF
Created/updated ${NAMESPACE}/${SECRET_NAME}.

Run the database bootstrap on restricted-node-6dc3f140f9 with:

  ssh restricted-node-6dc3f140f9@192.0.2.169 \\
    "sudo -iu postgres psql -X -v ON_ERROR_STOP=1 -v supabase_password='${POSTGRES_PASSWORD}' -d ${POSTGRES_DB} -f -" \\
    < sql/020_supabase_pg_re8ch_bootstrap.sql

Dashboard username: ${DASHBOARD_USERNAME}
Dashboard password is stored in the Kubernetes Secret key DASHBOARD_PASSWORD.
EOF
