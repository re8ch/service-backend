# BiziOps observations

This directory turns the existing public-site catalog into recurring business
observations. It deliberately does not introduce another browser event
collector. Each source remains authoritative for the facts it already owns.

`sources.yaml` records how facts can be read from Cloudflare, Google Analytics,
PostHog and other systems. `observe.py` always performs public HTTP/TLS checks
and records unconfigured provider integrations as explicit `unknown` facts.

Run locally:

```sh
python operations/biziops/observe.py \
  --output /tmp/biziops-observation.json \
  --summary /tmp/biziops-summary.md
```

The scheduled GitHub workflow uploads the JSON evidence as an artifact and
updates a rolling issue labelled `biziops-observation`. A GitHub Project can
auto-add that label without giving the workflow a broad project token.

Raw visitor events do not belong in Git. The checked-in files contain only
source declarations, acquisition status, aggregate observations and links to
the provider authority.

