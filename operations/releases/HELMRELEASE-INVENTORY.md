# HelmRelease transition inventory

> **Historical transition inventory.** This records the completed Wave 4 input
> classification. It is not current delivery guidance and must not be used to
> restore HelmRelease, Flux, or another cluster-local writer. Current delivery
> uses reviewed immutable bundles and the bounded Cloud SSA executor.

`helmrelease-inventory.yaml` is the Git declaration baseline for Wave 4 of the
GitHub-first control-plane migration. It is generated without cluster access and
contains no inline Helm values. Inline values are represented only by a SHA-256
digest; `valuesFrom` entries expose resource references but never referenced
data.

For the transition audit, each stable release ID had exactly one explicit decision in
`helmrelease-classifications.yaml`. Unknown releases fail validation instead of
defaulting to static execution. A `bundle-ssa-candidate` classification is not
cutover approval: live Helm history, finalizers, managed fields, rendered
inventory, image digests, deterministic bundle evidence and a rollback drill
remain required before the shared executor may write.

If maintaining the historical audit fixture, refresh and verify it with:

```sh
python operations/releases/helmrelease_inventory.py --write
python operations/releases/helmrelease_inventory.py --check
pytest -q tests/test_helmrelease_inventory.py
```

Live evidence must be collected through the read-only platform path and stored
as a restricted workflow artifact when it contains topology or operational
metadata. Do not copy Secret data or Helm storage payloads into this inventory.
