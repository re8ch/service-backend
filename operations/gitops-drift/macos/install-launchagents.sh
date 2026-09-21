#!/bin/sh
set -eu

root=${RE8CH_SERVICE_BACKEND_ANCHOR:-/Volumes/A1/service-backend}
support="$HOME/Library/Application Support/RE8CH/bin"
agents="$HOME/Library/LaunchAgents"
logs="$HOME/Library/Logs"
domain="gui/$(id -u)"

mkdir -p "$support" "$agents" "$logs"
install -m 0755 "$root/operations/workspace-controller/sync-main-anchor.sh" "$support/sync-main-anchor.sh"
install -m 0755 "$root/operations/gitops-drift/run-audit.sh" "$support/run-gitops-drift-audit.sh"
install -m 0755 "$root/operations/gitops-drift/audit.py" "$support/gitops-drift-audit.py"
install -m 0644 "$root/operations/workspace-controller/macos/com.re8ch.service-backend-anchor-sync.plist" "$agents/"
install -m 0644 "$root/operations/gitops-drift/macos/com.re8ch.gitops-drift.plist" "$agents/"

for label in com.re8ch.service-backend-anchor-sync com.re8ch.gitops-drift; do
  launchctl bootout "$domain" "$agents/$label.plist" 2>/dev/null || true
  launchctl bootstrap "$domain" "$agents/$label.plist"
done
