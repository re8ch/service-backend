#!/bin/sh
set -eu

anchor=${RE8CH_SERVICE_BACKEND_ANCHOR:-/Volumes/A1/service-backend}
remote=${RE8CH_SERVICE_BACKEND_REMOTE:-github}
branch=${RE8CH_SERVICE_BACKEND_BRANCH:-main}

fail() { printf '%s\n' "anchor-sync: $*" >&2; exit 1; }
unlock_anchor() {
  find "$anchor" -path "$anchor/.git" -prune -o -exec chmod u+w {} +
}
lock_anchor() {
  find "$anchor" -path "$anchor/.git" -prune -o -exec chmod a-w {} +
}
[ -d "$anchor/.git" ] || fail "missing Git anchor: $anchor"
[ "$(git -C "$anchor" rev-parse --abbrev-ref HEAD)" = "$branch" ] || fail "anchor is not on $branch"
[ -z "$(git -C "$anchor" status --porcelain)" ] || fail "anchor is dirty"

git -C "$anchor" fetch --quiet "$remote" "$branch"
target=$(git -C "$anchor" rev-parse "$remote/$branch")
current=$(git -C "$anchor" rev-parse HEAD)

if [ "$current" != "$target" ]; then
  unlock_anchor
  trap 'lock_anchor 2>/dev/null || true' EXIT HUP INT TERM
  git -C "$anchor" merge --ff-only "$remote/$branch"
fi

[ "$(git -C "$anchor" rev-parse HEAD)" = "$target" ] || fail "HEAD does not match $remote/$branch"
[ -z "$(git -C "$anchor" status --porcelain)" ] || fail "anchor became dirty"
lock_anchor
trap - EXIT HUP INT TERM
printf '%s\n' "anchor-sync: $(git -C "$anchor" rev-parse --short HEAD)"
