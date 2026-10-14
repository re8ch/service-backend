#!/usr/bin/env python3
"""Encrypt an approved App key directly from memory; never write cleartext YAML."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--age", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.app_id.isdecimal() or args.output.exists():
        raise SystemExit("invalid App ID or output already exists")
    if args.key.stat().st_mode & 0o077:
        raise SystemExit("private key must be owner-only")
    document = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
                "metadata": {"name": "api-conformance-github-app", "namespace": "api-conformance-system"},
                "stringData": {"app-id": args.app_id, "private-key.pem": args.key.read_text()}}
    result = subprocess.run(["sops", "--encrypt", "--input-type", "json", "--output-type", "yaml",
                             "--filename-override", str(args.output),
                             "--encrypted-regex", "^(data|stringData)$", "--age", args.age,
                             "--output", str(args.output), "/dev/stdin"],
                            input=json.dumps(document).encode(), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, check=False)
    if result.returncode:
        raise SystemExit("SOPS encryption failed; no secret output emitted")
    print("Encrypted App Secret created")


if __name__ == "__main__":
    main()
