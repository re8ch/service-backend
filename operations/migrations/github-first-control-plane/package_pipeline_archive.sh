#!/usr/bin/env bash
set -euo pipefail

for tool in age gh split tar zstd; do
  command -v "$tool" >/dev/null || { echo "missing required tool: $tool" >&2; exit 127; }
done

archive_dir="${1:?usage: package_pipeline_archive.sh ARCHIVE_DIR AGE_RECIPIENT}"
age_recipient="${2:?age recipient is required}"
test -s "$archive_dir/manifest.json"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
release="gitlab-service-backend-${stamp}"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT

tar -C "$archive_dir" -cf - . | zstd -19 -T0 | age -r "$age_recipient" | \
  split -b 1900m -d -a 4 - "$stage/${release}.part-"
cp "$archive_dir/manifest.json" "$stage/${release}.manifest.json"
(
  cd "$stage"
  shasum -a 256 "${release}.manifest.json" "${release}.part-"* > "${release}.sha256"
)

gh release create "$release" "$stage"/* \
  --repo re8ch/gitlab-pipeline-archive \
  --title "GitLab service-backend pipeline archive ${stamp}" \
  --notes "Encrypted, checksum-indexed audit export through the manifest cutoff."
