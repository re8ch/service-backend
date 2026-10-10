#!/usr/bin/env python3
"""Scan the two pinned Commerce images using CI-held Harbor credentials."""

import argparse
import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


PROJECT = "hyperswitch"
REPOSITORIES = ("processor-commerce-api", "processor-payment-registry-api")
BASE = "https://registry.re8ch.com/api/v2.0"


def api(method, path):
    username = os.environ["HARBOR_USERNAME"]
    password = os.environ["HARBOR_PASSWORD"]
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    request = urllib.request.Request(
        f"{BASE}/{path}", method=method,
        headers={"Authorization": f"Basic {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            return response.status, json.loads(body) if body else None
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Harbor {method} returned HTTP {error.code}") from None


def scan(repository, digest, output):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Expected immutable sha256 digest")
    artifact = (
        f"projects/{PROJECT}/repositories/{repository}/artifacts/"
        + urllib.parse.quote(digest, safe="")
    )
    _, current = api("GET", artifact + "?with_scan_overview=true")
    overviews = list((current.get("scan_overview") or {}).values())
    if not overviews or any(item.get("scan_status") != "Success" for item in overviews):
        api("POST", artifact + "/scan")
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        _, current = api("GET", artifact + "?with_scan_overview=true")
        overview = current.get("scan_overview") or {}
        scans = list(overview.values())
        if scans and all(item.get("scan_status") == "Success" for item in scans):
            _, report = api("GET", artifact + "/additions/vulnerabilities")
            if not isinstance(report, dict) or not any(
                isinstance(value, dict) and isinstance(value.get("vulnerabilities"), list)
                for value in report.values()
            ):
                raise RuntimeError("Harbor returned no complete vulnerability report")
            result = {"repository": repository, "digest": digest, "scan_overview": overview,
                      "vulnerability_report": report}
            (output / f"{repository}.json").write_text(json.dumps(result, indent=2))
            print(f"{repository}@{digest}: Harbor scan complete")
            return
        if any(item.get("scan_status") in ("Error", "Stopped") for item in scans):
            raise RuntimeError(f"Harbor scan failed for {repository}")
        time.sleep(10)
    raise TimeoutError(f"Harbor scan did not complete for {repository}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("commerce_digest")
    parser.add_argument("registry_digest")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for repository, digest in zip(REPOSITORIES,
                                  (args.commerce_digest, args.registry_digest)):
        scan(repository, digest, args.output)


if __name__ == "__main__":
    main()
