#!/usr/bin/env python3
"""Read-only GitHub Enterprise repository security and admission audit."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


API = "https://api.github.com"
GRAPHQL = f"{API}/graphql"
VERSION = "2022-11-28"


class Client:
    def __init__(self, token: str, pause: float = 0.05):
        self.token = token
        self.pause = pause

    def request(self, url: str, *, payload: dict | None = None) -> tuple[int, object]:
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": VERSION,
                "User-Agent": "re8ch-enterprise-security-audit",
            },
            method="POST" if payload is not None else "GET",
        )
        for attempt in range(5):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    body = response.read()
                    result = json.loads(body) if body else None
                    status = response.status
                break
            except urllib.error.HTTPError as error:
                body = error.read()
                try:
                    result = json.loads(body) if body else None
                except json.JSONDecodeError:
                    result = {"message": body.decode(errors="replace")[:500]}
                status = error.code
                break
            except (
                http.client.RemoteDisconnected,
                TimeoutError,
                socket.timeout,
                urllib.error.URLError,
            ):
                if attempt == 4:
                    raise
                time.sleep(2**attempt)
        time.sleep(self.pause)
        return status, result

    def get(self, path: str) -> tuple[int, object]:
        return self.request(f"{API}{path}")

    def graphql(self, query: str, variables: dict) -> dict:
        status, result = self.request(
            GRAPHQL, payload={"query": query, "variables": variables}
        )
        if status != 200 or not isinstance(result, dict) or result.get("errors"):
            raise RuntimeError(f"GraphQL audit failed ({status}): {result}")
        return result["data"]

    def pages(self, path: str) -> list[dict]:
        page = 1
        values: list[dict] = []
        while True:
            separator = "&" if "?" in path else "?"
            status, result = self.get(f"{path}{separator}per_page=100&page={page}")
            if status != 200 or not isinstance(result, list):
                raise RuntimeError(f"pagination failed for {path} ({status}): {result}")
            values.extend(result)
            if len(result) < 100:
                return values
            page += 1


def endpoint(client: Client, path: str) -> dict:
    status, result = client.get(path)
    return {"status": status, "value": result if status == 200 else None}


def organizations(client: Client, enterprise: str) -> list[str]:
    query = """
      query($slug: String!, $after: String) {
        enterprise(slug: $slug) {
          organizations(first: 100, after: $after) {
            nodes { login }
            pageInfo { hasNextPage endCursor }
          }
        }
      }
    """
    result: list[str] = []
    cursor = None
    while True:
        connection = client.graphql(query, {"slug": enterprise, "after": cursor})[
            "enterprise"
        ]["organizations"]
        result.extend(node["login"] for node in connection["nodes"])
        if not connection["pageInfo"]["hasNextPage"]:
            return result
        cursor = connection["pageInfo"]["endCursor"]


def audit_repository(client: Client, repository: dict) -> dict:
    full_name = repository["full_name"]
    branch = urllib.parse.quote(repository["default_branch"], safe="")
    base = f"/repos/{full_name}"
    return {
        "repository": full_name,
        "visibility": repository["visibility"],
        "archived": repository["archived"],
        "fork": repository["fork"],
        "defaultBranch": repository["default_branch"],
        "securityAndAnalysis": repository.get("security_and_analysis"),
        "dependabotAlerts": endpoint(client, f"{base}/vulnerability-alerts"),
        "effectiveDefaultBranchRules": endpoint(
            client, f"{base}/rules/branches/{branch}"
        ),
        "classicDefaultBranchProtection": endpoint(
            client, f"{base}/branches/{branch}/protection"
        ),
        "actionsPermissions": endpoint(client, f"{base}/actions/permissions"),
        "workflowPermissions": endpoint(
            client, f"{base}/actions/permissions/workflow"
        ),
        "codeScanningDefaultSetup": endpoint(
            client, f"{base}/code-scanning/default-setup"
        ),
        "environments": endpoint(client, f"{base}/environments"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enterprise", default="re8ch")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pause", type=float, default=0.05)
    args = parser.parse_args()
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise SystemExit("GH_TOKEN is required")

    client = Client(token, args.pause)
    orgs = organizations(client, args.enterprise)
    org_records = []
    repository_records = []
    for organization in orgs:
        rulesets = endpoint(client, f"/orgs/{organization}/rulesets")
        ruleset_details = []
        if rulesets["status"] == 200:
            for ruleset in rulesets["value"]:
                ruleset_details.append(
                    endpoint(
                        client,
                        f"/orgs/{organization}/rulesets/{ruleset['id']}",
                    )
                )
        org_records.append(
            {
                "organization": organization,
                "rulesets": rulesets,
                "rulesetDetails": ruleset_details,
                "actionsPermissions": endpoint(
                    client, f"/orgs/{organization}/actions/permissions"
                ),
                "workflowPermissions": endpoint(
                    client, f"/orgs/{organization}/actions/permissions/workflow"
                ),
            }
        )
        repositories = client.pages(f"/orgs/{organization}/repos?type=all")
        repository_records.extend(
            audit_repository(client, repository) for repository in repositories
        )

    report = {
        "apiVersion": "governance.re8ch.com/v1alpha1",
        "kind": "GitHubEnterpriseSecurityAudit",
        "enterprise": args.enterprise,
        "readOnly": True,
        "organizations": org_records,
        "repositories": repository_records,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
