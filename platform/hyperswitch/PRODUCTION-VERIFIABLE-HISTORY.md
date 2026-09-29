# Production and verifiable payment history acceptance contract

This contract gates the sandbox-to-production change. The current HelmRelease
remains sandbox until every gate has evidence and a reviewed GitOps PR.

## Authorities and data flow

1. Hyperswitch owns payment attempts and connector interaction. The RE8CH
   payment registry in PostgreSQL owns the canonical payment event sequence,
   status projection and transactional delivery outbox. Product fulfillment
   consumes only verified terminal events from that outbox.
2. Every accepted event receives one stable `event_id`, a canonical versioned
   JSON representation and a SHA-256 digest inside the same PostgreSQL
   transaction as the payment projection. The public bytes include source,
   hashed merchant/source identifiers, payment ID, attempt ID, event type,
   occurred/received times, verification result, allowlisted payment facts and
   digests of the full normalized and raw provider payloads. Retries must
   return the existing event without changing its bytes or sequence number.
3. An independent worker reads a dedicated PostgreSQL audit outbox in sequence
   order and writes each canonical event under a never-reused key to immudb
   using a verified write. It persists the immudb transaction ID and verified
   state in PostgreSQL only after immudb confirms the write. On retry, the
   worker compares the stored bytes and hash; a mismatch is a fatal integrity
   alert. No payment webhook should acknowledge a terminal state unless its
   PostgreSQL event and audit outbox entry committed.
4. Each verified immudb event is promptly anchored on Base mainnet (chain ID
   8453), funded with native ETH. Test on Base Sepolia (chain ID 84532) first.
   An Ethereum mainnet checkpoint worker periodically closes ordered lists of
   committed events only after every member is independently readable from immudb and
   its Base anchor has reached the required finality. It computes a
   domain-separated Merkle tree over the exact canonical event bytes,
   records tree version, leaf count, range and root, and makes the batch
   immutable. This root is anchored on Ethereum mainnet (chain ID 1).
5. Each anchor receipt records chain ID, contract or transaction data format,
   transaction hash, block number/hash and finality policy. A reorganization
   invalidates the claimed receipt and raises an alert; the immutable root is
   never rewritten. The API reports receipt claims, while the verifier checks
   chain finality using an independently chosen RPC. A Base anchor is treated
   as final only when its block is at or below Base's finalized head, derived
   from finalized Ethereum input data.
   The EVM signing wallet is a separate restricted service. Its encrypted
   wallet key is released only to this service at runtime. Never log or export
   the key.

## Public verification contract

`GET /v1/verification/transactions/{payment_id}` returns event IDs and sequences.
`GET /v1/verification/events/{event_id}` returns the canonical event bytes,
SHA-256 digest, immudb transaction ID and consistency proof, Base event anchor,
Merkle leaf index, siblings and root, batch range, and Ethereum checkpoint
receipt. Unanchored events are clearly marked pending. The browser recomputes
the record hash and Merkle proof and queries both chains through visitor-chosen
RPC endpoints. A downloadable immudb proof can be checked offline with an
independently obtained signing public key. The API's receipt status is a claim,
not a trusted `verified` flag.

The public representation must contain no card data, connector credentials,
webhook headers, payer identity or unredacted provider payload. If raw provider
payload is retained, it needs an authenticated access path and its digest must
be bound to the public canonical event. Public verification proves the disclosed
representation and raw payload digest; it cannot prove undisclosed raw bytes.

Publish the canonical encoding, domain separators, SHA-256 Merkle rules,
batch format, chain ID and anchor transaction format as a versioned specification.
Keep independently retrievable batch manifests so omission can be detected by
enumerating the contiguous sequence from genesis to the latest anchored batch.
The verifier must check event hash, Merkle path, batch continuity, transaction
inclusion, chain ID and finality using its own trusted chain RPC endpoint.

## Production gates

- Production merchant approval and connector credentials, webhook verification,
  refund, replay, duplicate delivery and reconciliation tests are complete.
- The pinned Superposition v0.112.0 PROD path calls AWS KMS. For this deployment,
  build and test a Superposition variant that decrypts its startup secrets
  with a locally held static key. The key must be injected from a read-only
  host-protected file at startup, never committed, logged or bundled into an
  image. The operator keeps an offline copy for recovery and rotates it using
  a documented re-encryption procedure. No cloud KMS is required. PostgreSQL/immudb
  backups, recovery drill, TOTP and least-privilege roles are proven.
- The immudb worker includes every committed event from genesis; PostgreSQL
  identity numbers may have gaps after aborted transactions. The public verifier
  rejects changed bytes, reordered leaves, omitted events, incorrect proofs,
  mismatched roots and a reorged anchor.
- A low-value real transaction and refund have end-to-end evidence, including
  PostgreSQL, immudb, finalized chain receipt and independent verification.
- Flux, signed commits, CI and PR review complete before changing Router
  `run_env`, Control Center `is_live_mode` or Superposition `app_env`.
