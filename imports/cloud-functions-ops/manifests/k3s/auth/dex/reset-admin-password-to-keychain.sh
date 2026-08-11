#!/usr/bin/env bash
set -euo pipefail

# Recover the Dex static administrator without printing the generated password.
# The plaintext is stored only in macOS Keychain and the clipboard for the
# immediate browser login; Kubernetes receives only its bcrypt hash.
: "${DEX_SSH_TARGET:=restricted-node-6dc3f140f9}"
: "${DEX_KEYCHAIN_SERVICE:=re8ch-dex-admin}"

password=$(openssl rand -base64 30 | tr -d '\n')
/usr/bin/security add-generic-password -U \
  -a admin@re8ch.com -s "$DEX_KEYCHAIN_SERVICE" -w "$password" >/dev/null
printf %s "$password" | pbcopy
password_hash=$(/usr/sbin/htpasswd -bnBC 12 '' "$password" | tr -d ':\n')

ssh "$DEX_SSH_TARGET" "sudo env DEX_ADMIN_HASH='$password_hash' python3 - <<'PY'
import base64
import json
import os
import subprocess
import yaml

new_hash = os.environ['DEX_ADMIN_HASH']
raw = subprocess.check_output([
    'k3s', 'kubectl', '-n', 'dex', 'get', 'secret', 'dex-config', '-o', 'json'
])
secret = json.loads(raw)
config = yaml.safe_load(base64.b64decode(secret['data']['config.yaml']))
for user in config.get('staticPasswords', []):
    if user.get('email') == 'admin@re8ch.com':
        user['hash'] = new_hash
        break
else:
    raise SystemExit('Dex admin static user not found')
secret['data']['config.yaml'] = base64.b64encode(
    yaml.safe_dump(config, sort_keys=False).encode()
).decode()
secret['metadata'] = {'name': 'dex-config', 'namespace': 'dex'}
subprocess.run(
    ['k3s', 'kubectl', 'apply', '-f', '-'],
    input=json.dumps(secret).encode(),
    check=True,
    stdout=subprocess.DEVNULL,
)
PY"

ssh "$DEX_SSH_TARGET" \
  'sudo k3s kubectl -n dex rollout restart deployment/dex >/dev/null && sudo k3s kubectl -n dex rollout status deployment/dex --timeout=180s'
echo "Dex administrator password recovered to Keychain service: $DEX_KEYCHAIN_SERVICE"
