#!/usr/bin/env bash
# Shallow PR checkouts may omit the event base after main advances. Fetch only
# enough ancestry for that exact event comparison; never substitute a newer base.
set -euo pipefail
base="${1:?event base SHA required}"
head="${2:?event head SHA required}"
[[ "$base" =~ ^[0-9a-f]{40}$ && "$head" =~ ^[0-9a-f]{40}$ ]] || { echo 'Expected immutable event SHAs' >&2; exit 1; }
[[ "$(git rev-parse HEAD)" == "$head" ]] || { echo 'Checkout does not match event head' >&2; exit 1; }
if git merge-base "$base" "$head" >/dev/null 2>&1; then exit 0; fi
for depth in 16 64 256 1024; do
  git fetch --no-tags --deepen="$depth" origin "$base" "$head"
  if git merge-base "$base" "$head" >/dev/null 2>&1; then exit 0; fi
done
# Rare old branches need complete ancestry; ordinary PRs stop after one fetch.
if [[ "$(git rev-parse --is-shallow-repository)" == true ]]; then
  git fetch --no-tags --unshallow origin "$base" "$head"
fi
git merge-base "$base" "$head" >/dev/null
