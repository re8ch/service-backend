#!/usr/bin/env python3
"""Read-only checks for public MCP resources used by third-party clients."""

import json
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit


ORIGIN = "https://tools.service.re8ch.com"
RESOURCES = ("cloud", "infra", "orchestra", "tenant")


def fetch(url):
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "re8ch-mcp-discovery-check/1"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def authorization_metadata_url(issuer):
    parts = urlsplit(issuer)
    return urlunsplit((parts.scheme, parts.netloc,
                      "/.well-known/oauth-authorization-server" + parts.path.rstrip("/"), "", ""))


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
            else:
                issuer = issuers[0]
                auth_url = authorization_metadata_url(issuer)
                auth = fetch(auth_url)
                if auth.get("issuer") != issuer:
                    failures.append(f"{name}: authorization issuer mismatch at {auth_url}")
                if "S256" not in auth.get("code_challenge_methods_supported", []):
                    failures.append(f"{name}: authorization server must support PKCE S256")
            print(f"{name}: resource={metadata.get('resource')} issuer={issuers}")
        except (OSError, ValueError, KeyError) as exc:
            failures.append(f"{name}: metadata unavailable: {exc}")
    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
