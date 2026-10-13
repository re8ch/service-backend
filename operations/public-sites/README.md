# Public website governance

`inventory/public-sites.yaml` is the reviewed desired-state registry for public
websites. It links each logical site to its repository and independently
deployable global or China delivery surfaces. It contains identifiers only;
credentials and DNS verification values stay in their existing secret stores.

Validate and reconcile it with the read-only EdgeOne/ESA/Cloudflare snapshot:

```sh
python operations/public-sites/validate.py
python operations/public-sites/reconcile.py
```

The generated `platform/headlamp/public-site-governance.yaml` is consumed by the
Headlamp Public Edge plugin. Provider refresh is observation-only and must run
before reconciliation when a fresh cloud snapshot is required.

Quality profiles are advisory. Build, test, upload, and post-upload artifact
integrity may fail a release; Lighthouse, accessibility, SEO, GEO, and content
scores must not be configured as required GitHub checks.

## Allocation policy

- Allocate one origin bucket or Cloudflare project per independently released
  site deployment.
- Multiple hostnames may share an origin only when artifact, owner, cache,
  cleanup, and rollback policy are identical.
- RE8CH-family China sites default to COS + EdgeOne. Existing `ynzs.com` family
  sites remain on OSS + ESA unless a reviewed migration says otherwise.
- Prefer Cloudflare Pages for pure global static sites and Workers when edge
  routing or request logic is part of the site.
