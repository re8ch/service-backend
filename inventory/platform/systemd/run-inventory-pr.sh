#!/bin/sh
set -eu

branch=inventory-bot/live-drift
git fetch origin master "$branch" || git fetch origin master
if git show-ref --verify --quiet "refs/remotes/origin/$branch"; then
  git switch --force-create "$branch" "origin/$branch"
else
  git switch --force-create "$branch" origin/master
fi
python3 scripts/platform_inventory.py --kubectl "kubectl --kubeconfig /etc/re8ch-inventory/kubeconfig"
git add manifests/platform-inventory/live manifests/platform-inventory/generated
if git diff --cached --quiet; then
  exit 0
fi
git -c user.name='RE8CH Inventory Bot' -c user.email='inventory-bot@re8ch.com' commit -m 'chore(inventory): reconcile live platform inventory'
git push origin "$branch"
