# Stalwart mail security baseline

This baseline keeps mail delivery available while enabling every security and
transport mechanism that is safe without external prerequisites.

## Active controls

- SPF, DKIM (RSA and Ed25519), DMARC and reverse-DNS verification run in
  relaxed mode for inbound SMTP. ARC chains are verified in relaxed mode.
- Authenticated outbound mail is DKIM signed. The built-in spam filter,
  DNSBL/URIBL rules and phishing checks run for unauthenticated inbound mail.
  Scores at 5 enter Junk and scores at 15 are rejected; silent discard remains
  disabled.
- Outbound STARTTLS, DANE and MTA-STS are optional. “Optional” still enforces a
  recipient domain's valid DANE or MTA-STS policy, but does not reject delivery
  to domains that publish neither mechanism.
- Hosted-domain MTA-STS is published in `testing` mode and TLS-RPT is sent to
  each domain's report address. DKIM, MTA-STS and TLS-RPT DNS records are
  refreshed through the domain-scoped Cloudflare credentials.
- Automatic bans expire. Authentication bans last 15 minutes; scan and loiter
  bans last one hour; SMTP abuse bans last 12 hours.
- Prometheus metrics are scraped with Basic authentication through the
  cluster-internal management Service at `/metrics/prometheus`; the same path
  remains authenticated even if a public HTTP route overlaps it.

## Activation gates

Do not move MTA-STS from `testing` to `enforce` until at least two weeks of
TLS-RPT reports show that every advertised MX presents a valid certificate and
accepts STARTTLS. Do not globally set outbound DANE, MTA-STS or STARTTLS to
`require`: that would make delivery to domains without those policies fail.

Publish TLSA records only after the relevant zone has DNSSEC and every MX name,
certificate and renewal path is aligned. `phonaid.com` has DNSSEC today;
`re8ch.com` does not. DANE remains active for outbound delivery in optional
mode, while inbound TLSA publication stays gated.

Move DMARC from monitoring to quarantine/reject only after aggregate reports
show that every legitimate sender is aligned. `phonaid.com` is already at
`p=reject`; `re8ch.com` remains at `p=none` until that evidence exists.

ARC sealing is intentionally absent: Stalwart v1 verifies ARC but no longer
adds ARC sets. It signs relayed mail with DKIM/DKIM2 instead.

## Verification and rollback

Verify public SPF, DKIM, DMARC, TLS-RPT and MTA-STS records with an independent
resolver. Fetch `https://mta-sts.<domain>/.well-known/mta-sts.txt`, verify the
certificate and confirm the mode is `testing`. Confirm the Prometheus target is
up and watch inbound rejects, Junk placement, outbound queue size and TLS
failures.

If false positives occur, set `scoreReject` back to `0`; Junk classification
continues. If authentication clients are blocked, reduce the ban period or
remove the affected address from the block list. If MTA-STS publication is
wrong, set its mode to `disable`, refresh the DNS record, and keep accepting
SMTP while the cached testing policy expires.
