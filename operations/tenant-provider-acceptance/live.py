#!/usr/bin/env python3
"""Run tenant CI with the existing in-cluster test identity; never log secrets."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request


def secret(namespace, name):
    command = ["ssh", "r640", "sudo -n kubectl", "-n", namespace, "get", "secret", name, "-o", "json"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f"cannot read {namespace}/{name} test secret")
    try:
        data = json.loads(result.stdout)["data"]
        return {key: base64.b64decode(value).decode() for key, value in data.items()}
    except (KeyError, ValueError) as exc:
        raise RuntimeError(f"invalid {namespace}/{name} test secret") from exc


def user_token(identity_user, anon_key):
    payload = json.dumps({"email": identity_user["email"], "password": identity_user["password"]}).encode()
    request = urllib.request.Request(
        "https://db-rw.re8ch.com/auth/v1/token?grant_type=password",
        data=payload,
        headers={"Content-Type": "application/json", "apikey": anon_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)["access_token"]
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"test user login returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, KeyError) as exc:
        raise RuntimeError("test user login endpoint unavailable") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("contract", "full"), required=True)
    parser.add_argument("--mcp-url", default="", help="override the contract MCP endpoint")
    parser.add_argument("--resume-suffix", default="")
    parser.add_argument("--resume-provider", choices=("harbor", "grafana"), default="")
    parser.add_argument("--skip-preflight", action="store_true")
    args = parser.parse_args()
    try:
        identity_user = secret("tenant-infrastructure", "tenant-provider-acceptance-user")
        identity = secret("supabase-identity", "supabase-identity-secrets")
        token = user_token(identity_user, identity["ANON_KEY"])
        environment = {
            **os.environ,
            "TENANT_ACCESS_TOKEN": token,
            "IDENTITY_USER_TOKEN": token,
            "IDENTITY_USER_REF": identity_user["user-id"],
            "IDENTITY_SERVICE_KEY": identity["SERVICE_ROLE_KEY"],
            "TENANT_REF": "tenant-ci",
            "HARBOR_PROJECT": "tenant-ci",
            "GRAFANA_DATASOURCE_UID": "app",
            "GRAFANA_ORG_ID": "13",
        }
        if args.mode == "full":
            environment["ALLOW_LIVE_MUTATION"] = "1"
        script = "contract.py" if args.mode == "contract" else "run.py"
        command = [sys.executable, os.path.join(os.path.dirname(__file__), script)]
        if args.mcp_url:
            command += ["--mcp-url", args.mcp_url]
        if args.mode == "full" and args.resume_provider:
            command += ["--resume-suffix", args.resume_suffix, "--resume-provider", args.resume_provider]
            if args.skip_preflight:
                command.append("--skip-preflight")
        result = subprocess.call(command, env=environment)
        if result or args.mode != "contract" or args.mcp_url:
            return result
        return subprocess.call([
            sys.executable, os.path.join(os.path.dirname(__file__), "contract.py"),
            "--mcp-url", "https://tools.service.re8ch.com/tenant/mcp", "--publication-only",
        ], env=environment)
    except (RuntimeError, KeyError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
