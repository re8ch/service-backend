# k6 functional contracts

`clusters/re8ch/components/synthetic-monitoring/contracts.json` is the reviewed dataset of read-only public behavior. The k6
runner exercises each contract three times with one virtual user, every 30
minutes in the cluster and from GitHub Actions, and on every pull request. A failed status, response assertion, or
95th-percentile latency over fifteen seconds fails the workflow. This is a
functional drift signal, not a load test.

The contracts file is versioned by `operations/k6/revisions.json`. For each change, increment
`version`, append a revision with the previous digest, the new SHA-256 of the
exact `contracts.json` bytes, and a reason. The validator checks the digest
chain and, on pull requests, confirms that prior revisions remain unchanged.
This records changes to the test dataset as well as changes to the system.

Run locally from the repository root:

```sh
python operations/k6/validate.py
docker run --rm -v "$PWD/clusters/re8ch/components/synthetic-monitoring:/work:ro" -w /work grafana/k6:1.3.0 run smoke.js
```

The first contracts cover the public OIDC discovery document and the expected
unauthenticated Cloud MCP challenge. Probes use public DNS and require no
secrets or cluster credentials. Add authenticated and internal workflows only
with scoped test identities and an approved runner network path. Keep raw
response bodies and credentials out of Git and workflow artifacts.
