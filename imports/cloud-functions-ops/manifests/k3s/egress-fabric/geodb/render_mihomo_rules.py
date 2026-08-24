#!/usr/bin/env python3
"""Render the pinned GeoDB CN CIDRs as a Mihomo classical rule provider."""
import gzip
import json
import pathlib

root = pathlib.Path(__file__).resolve().parent
with gzip.open(root / "geodb.json.gz", "rt", encoding="utf-8") as src:
    snapshot = json.load(src)
lines = ["payload:"]
lines.extend(f"  - IP-CIDR,{cidr},no-resolve" for cidr in snapshot["cn_cidrs"])
(root / "cn-ip-rules.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
