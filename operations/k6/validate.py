#!/usr/bin/env python3
"""Validate the probe catalog and its append-only change history."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CONTRACT_PATH = "clusters/re8ch/components/synthetic-monitoring/contracts.json"
REVISION_PATH = "operations/k6/revisions.json"
CONTRACT_FILE = ROOT / CONTRACT_PATH


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate():
    raw = CONTRACT_FILE.read_bytes()
    catalog = json.loads(raw)
    revisions = load(HERE / "revisions.json")["revisions"]
    assert catalog["version"] == len(revisions) and revisions, "version must match revision count"
    ids = set()
    for probe in catalog["probes"]:
        assert probe["id"] not in ids, "duplicate probe ID"
        ids.add(probe["id"])
        url = urlparse(probe["url"])
        assert url.scheme == "https" and url.hostname and not url.username and not url.password, "HTTPS probe URL required"
        assert probe["status"] in (200, 401), "unexpected status contract"
        assert ("jsonField" in probe) == ("equals" in probe), "incomplete JSON assertion"
        assert ("header" in probe) == ("contains" in probe), "incomplete header assertion"
        assert "jsonField" in probe or "header" in probe, "status alone is insufficient"
    assert ids, "at least one probe required"
    previous = None
    for index, revision in enumerate(revisions, 1):
        assert revision["number"] == index, "revision sequence must be continuous"
        assert revision["previousSha256"] == previous, "revision hash chain broken"
        assert len(revision["reason"].strip()) >= 12, "revision reason required"
        digest = revision["contractSha256"]
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest), "invalid digest"
        previous = digest
    assert previous == hashlib.sha256(raw).hexdigest(), "catalog digest does not match latest revision"
    return revisions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", help="PR base commit for append-only validation")
    args = parser.parse_args()
    revisions = validate()
    if args.base:
        try:
            old_raw = subprocess.check_output(["git", "show", f"{args.base}:{REVISION_PATH}"], cwd=HERE, stderr=subprocess.DEVNULL)
            old_revisions = json.loads(old_raw)["revisions"]
        except subprocess.CalledProcessError:
            old_revisions = []
        assert revisions[:len(old_revisions)] == old_revisions, "past revisions may not be edited"
        old_contract = subprocess.run(
            ["git", "show", f"{args.base}:{CONTRACT_PATH}"], cwd=HERE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
        )
        if old_contract.returncode == 0 and old_contract.stdout != CONTRACT_FILE.read_bytes():
            assert len(revisions) > len(old_revisions), "contract change requires a new revision"
    print(f"Validated {len(revisions)} contract revision(s) and {len(load(CONTRACT_FILE)['probes'])} probe(s)")


if __name__ == "__main__":
    main()
