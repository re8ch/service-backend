#!/usr/bin/env python3
"""Read-only checks for public MCP resources used by third-party clients."""

import json
import sys
import urllib.error
import urllib.request


ORIGIN = "https://tools.service.re8ch.com"
RESOURCES = ("cloud", "tenant")


def fetch(url):
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "re8ch-mcp-discovery-check/1"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def main():
    failures = []
    for name in RESOURCES:
        expected = f"{ORIGIN}/{name}/mcp"
        metadata_url = f"{ORIGIN}/.well-known/oauth-protected-resource/{name}/mcp"
        try:
            metadata = fetch(metadata_url)
            if metadata.get("resource") != expected:
                failures.append(f"{name}: resource is {metadata.get('resource')!r}, expected {expected!r}")
            issuers = metadata.get("authorization_servers")
            if not isinstance(issuers, list) or len(issuers) != 1 or not issuers[0].startswith("https://"):
                failures.append(f"{name}: expected one HTTPS authorization server")
            print(f"{name}: resource={metadata.get('resource')} issuer={issuers}")
            if isinstance(issuers, list) and len(issuers) == 1:
                discovery = fetch(issuers[0].rstrip("/") + "/.well-known/openid-configuration")
                print(f"{name}: registration_endpoint={discovery.get('registration_endpoint')}")
                if name == "tenant" and not discovery.get("registration_endpoint"):
                    failures.append("tenant: WorkBuddy requires dynamic client registration")
        except (OSError, ValueError, KeyError) as exc:
            failures.append(f"{name}: metadata unavailable: {exc}")
    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
