#!/usr/bin/env python3
"""Generate a Sign in with Apple client-secret JWT without logging secrets."""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature


MAX_TTL_SECONDS = 15_777_000
DEFAULT_TTL_SECONDS = 155 * 24 * 60 * 60


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--team-id", required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 1 <= args.ttl_seconds <= MAX_TTL_SECONDS:
        raise SystemExit(f"--ttl-seconds must be between 1 and {MAX_TTL_SECONDS}")
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output}")

    now = int(time.time())
    expires = now + args.ttl_seconds
    header = {"alg": "ES256", "kid": args.key_id}
    claims = {
        "iss": args.team_id,
        "iat": now,
        "exp": expires,
        "aud": "https://appleid.apple.com",
        "sub": args.client_id,
    }
    encoded_header = b64url(json.dumps(header, separators=(",", ":")).encode())
    encoded_claims = b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")

    private_key = serialization.load_pem_private_key(
        args.private_key.expanduser().read_bytes(), password=None
    )
    if not isinstance(private_key, ec.EllipticCurvePrivateKey):
        raise SystemExit("Apple private key is not an EC private key")
    der_signature = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_signature)
    size = (private_key.curve.key_size + 7) // 8
    raw_signature = r.to_bytes(size, "big") + s.to_bytes(size, "big")
    token = f"{signing_input.decode('ascii')}.{b64url(raw_signature)}"

    output = args.output.expanduser()
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(output.parent, 0o700)
    payload = {
        "provider": "apple",
        "client_id": args.client_id,
        "client_secret": token,
        "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat(),
        "key_id": args.key_id,
        "team_id": args.team_id,
        "private_key_path": str(args.private_key.expanduser().resolve()),
    }
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(payload, stream, indent=2)
        stream.write("\n")
    print(f"wrote Apple credential metadata to {output} (expires {payload['expires_at']})")


if __name__ == "__main__":
    main()
