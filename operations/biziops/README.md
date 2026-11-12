# BiziOps observations

This directory turns the existing public-site catalog into recurring business
observations. It deliberately does not introduce another browser event
collector. Each source remains authoritative for the facts it already owns.

`sources.yaml` records how facts can be read from Cloudflare, Google Analytics,
PostHog and other systems. `observe.py` always performs public HTTP/TLS checks
and records unconfigured provider integrations as explicit `unknown` facts.

## Video platforms

`spec.videoSources` declares one entry per video platform. Add the business's
own channels under `accounts` (`id` is the YouTube channel ID or `@handle`, or
the Bilibili numeric UID; `label` is display text). Each run reports followers,
views and video counts per account; a platform without accounts, without its
credential, or without a collector is shown as `unknown`, never `0`.

| Platform | Collector | Credential | Notes |
| --- | --- | --- | --- |
| YouTube | Data API v3 `channels.list` | `YOUTUBE_API_KEY` Actions secret | Official, free quota (10,000 units/day; one call = 1 unit). |
| Bilibili | public `x/relation/stat` | none | Unofficial; followers only; may be risk-controlled from data-centre IPs. |
| TikTok | declared | client key/secret + owner OAuth | Needs an approved Display API app. |
| 抖音 Douyin / 快手 Kuaishou | declared | open-platform app + owner OAuth | Needs mainland enterprise verification. |
| 微信视频号 | declared | — | No third-party metrics API; use the 视频号助手 export. |

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

