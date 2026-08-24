# cloud-functions history migration

On 2026-08-24, the history of `re8ch/cloud-functions` was split into:

- Nuclio/function history retained in the rewritten GitHub repository.
- Cluster and platform operations history imported into this repository.

The imported history first used `imports/cloud-functions-ops/` as a collision-
free prefix. A signed integration commit then moved the current files into
`clusters/`, `operations/`, `platform/`, `inventory/` and `runbooks/`.

## Provenance

- Original master: `9d2aa44fa7d848c23e4e7d756871b2716a0b8ac7`
- Rewritten cloud-functions master: `bd00c4ada735f5f0ba6d6d10955a72c0f00159bb`
- Imported operations master: `1c2f4fd47207fd22d36f1893586da486f6d3f13f`
- Imported inventory drift tip: `e188110923452adfe7dca67d925e35626c5f342d`
- Pre-rewrite bundle SHA-256:
  `f567d0b556450bce751bea7eea07d3532bd0c23df4bd73e80fe1b36ded3337de`

`nuclio-commit-map.txt` and `ops-commit-map.txt` map original commit IDs to
their rewritten IDs. A zero destination means that the commit became empty in
that side of the split. Ref maps provide the corresponding branch and tag
mapping.

The recovery bundle is intentionally stored outside Git because it contains the
complete pre-rewrite history. Existing cloud-functions clones must be replaced
with a fresh clone after cutover.
