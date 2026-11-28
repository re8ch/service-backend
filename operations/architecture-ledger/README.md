# Verified cluster architecture ledger

This module turns an exact deployment, a bounded live observation and a
comparable benchmark into an immutable architecture revision. Kubernetes is
runtime authority, GitHub holds provenance and coordination, and Hugging Face
receives only the redacted dataset projection used for later optimization.

Publication is deliberately manual in the first slice. A producer workflow
uploads an artifact named `cluster-architecture-evidence` containing
`record.json`. The publication workflow independently reads back the referenced
GitHub Deployment and its latest status before it creates a GitHub Release or
uploads anything to Hugging Face.

The record must contain:

- the exact source repository and 40-character source SHA;
- a successful GitHub Deployment id, environment and target cluster;
- an immutable cloud SSA receipt digest;
- a fresh live observation with at least one successful functional probe;
- a redacted architecture digest and component inventory;
- a benchmark profile digest and bounded performance metrics.

`previous.json` is optional. When supplied, performance deltas are emitted only
when the benchmark profile digests match. Incompatible profiles are recorded as
`not_comparable`, never as an improvement or regression.

The GitHub Environment `architecture-ledger-publication` owns `HF_TOKEN`. The
token must be able to write the private `RE8CH/cluster-architecture-ledger`
dataset. No Hugging Face credential or production topology belongs in Git.

Validate locally:

```sh
python -m pip install 'jsonschema>=4.23,<5'
python operations/architecture-ledger/ledger.py validate \
  operations/architecture-ledger/fixtures/record.json
python -m unittest operations/architecture-ledger/test_ledger.py
```
