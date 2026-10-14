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

## Static delivery contract

A static frontend has only two release shapes:

1. `direct`: the repository already contains the files to publish.
2. `compiled`: run the declared build command and publish its output directory.

Both shapes finish by replacing the target bucket or static-site project with
the output directory. A static site is not packaged as a container and does not
use Harbor or Kubernetes GitOps merely to copy files. Use the provider-native
path selected by `publish.tool`:

- `storage-file-api` or `firebase-cli` for the shared storage/file API and
  Firebase-backed buckets;
- `tencent-cos-api` for Tencent COS through COSCLI, the Tencent COS SDK/API,
  or the shared file API;
- `aliyun-ossutil` for Aliyun OSS;
- `cloudflare-wrangler` for Cloudflare Pages, Workers Static Assets, or R2.

The publisher synchronizes the complete output and removes objects absent from
the new output (`replace: true`). It must verify the uploaded entry document
before declaring success. Credentials stay in the selected protected runner or
environment and are never copied into the product repository.

Provider configuration is separate from ordinary content publication. Change
CORS, CSP/response headers, custom domains, cache rules, redirects, rewrites,
or EdgeOne/ESA/Cloudflare routing only when the site requires them, through the
provider's reviewed configuration path. A content-only release must not rewrite
those controls.

If a frontend requires a server process, SSR, private credentials, queues,
database access, or a coupled backend, it is not a static-site release. Deploy
that runtime with the provider-native runtime CLI or the digest-pinned
Kubernetes GitOps contract instead.

## Allocation policy

- Allocate one origin bucket or Cloudflare project per independently released
  site deployment.
- Multiple hostnames may share an origin only when artifact, owner, cache,
  cleanup, and rollback policy are identical.
- RE8CH-family China sites default to COS + EdgeOne. Existing `ynzs.com` family
  sites remain on OSS + ESA unless a reviewed migration says otherwise.
- Prefer Cloudflare Pages for pure global static sites and Workers when edge
  routing or request logic is part of the site.
