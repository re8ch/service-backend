#!/bin/sh
set -eu

repo_url=${1:?SSH repository URL required}
root=/opt/re8ch/cloud-functions-repo
config_dir=/etc/re8ch-inventory
account=re8ch-inventory

[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }
id "$account" >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/re8ch-inventory --shell /usr/sbin/nologin "$account"
install -d -m 0700 -o "$account" -g "$account" /var/lib/re8ch-inventory/.ssh
install -d -m 0750 -o root -g "$account" "$config_dir"

token=$(k3s kubectl -n toolhive-system create token platform-inventory-scanner --duration=8760h)
ca_data=$(base64 -w0 </var/lib/rancher/k3s/server/tls/server-ca.crt)
umask 0077
printf '%s\n' \
  'apiVersion: v1' \
  'kind: Config' \
  'clusters:' \
  '- name: re8ch-k3s' \
  '  cluster:' \
  '    server: https://127.0.0.1:6443' \
  "    certificate-authority-data: $ca_data" \
  'contexts:' \
  '- name: inventory@re8ch-k3s' \
  '  context: {cluster: re8ch-k3s, user: inventory}' \
  'current-context: inventory@re8ch-k3s' \
  'users:' \
  '- name: inventory' \
  "  user: {token: $token}" >"$config_dir/kubeconfig"
chown root:"$account" "$config_dir/kubeconfig"
chmod 0640 "$config_dir/kubeconfig"

ssh-keyscan -t ed25519 github.com >"$config_dir/known_hosts"
printf '%s\n' \
  'Host github.com' \
  '  HostName github.com' \
  '  User git' \
  '  IdentityFile /var/lib/re8ch-inventory/.ssh/deploy_key' \
  "  UserKnownHostsFile $config_dir/known_hosts" \
  '  IdentitiesOnly yes' >"$config_dir/ssh_config"
chmod 0644 "$config_dir/known_hosts" "$config_dir/ssh_config"

if [ ! -d "$root/.git" ]; then
  install -d -m 0755 -o "$account" -g "$account" /opt/re8ch
  runuser -u "$account" -- env GIT_SSH_COMMAND="ssh -F $config_dir/ssh_config" git clone "$repo_url" "$root"
fi
chown -R "$account":"$account" "$root"
install -m 0644 "$root/manifests/platform-inventory/systemd/re8ch-platform-inventory.service" /etc/systemd/system/
install -m 0644 "$root/manifests/platform-inventory/systemd/re8ch-platform-inventory.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now re8ch-platform-inventory.timer
systemctl start re8ch-platform-inventory.service
