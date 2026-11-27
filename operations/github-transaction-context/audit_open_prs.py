#!/usr/bin/env python3
"""Build a deterministic, read-only ledger of open PR transaction context."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
INVENTORY = Path(__file__).with_name("repository-inventory.json")
CONFIG = Path(__file__).with_name("config.json")

PR_QUERY = """
query($owner: String!, $repo: String!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequests(first: 50, after: $cursor, states: OPEN, orderBy: {field: CREATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number url title headRefName createdAt updatedAt
        author { __typename login }
        closingIssuesReferences(first: 10) {
          totalCount
          nodes {
            number url body
            repository { nameWithOwner }
            issueType { name }
            parent { number url repository { nameWithOwner } }
          }
        }
      }
    }
  }
}
"""

ISSUE_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) {
      projectItems(first: 10) {
        nodes {
          project {
            number title url
            owner { ... on Organization { login } ... on User { login } }
          }
          fieldValues(first: 20) {
            nodes {
              ... on ProjectV2ItemFieldSingleSelectValue {
                name
                field { ... on ProjectV2SingleSelectField { name } }
              }
              ... on ProjectV2ItemFieldDateValue {
                date
                field { ... on ProjectV2FieldCommon { name } }
              }
              ... on ProjectV2ItemFieldTextValue {
                text
                field { ... on ProjectV2FieldCommon { name } }
              }
            }
          }
        }
      }
    }
  }
}
"""


def graphql(token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "re8ch-open-pr-context-audit",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.load(response)
    if payload.get("errors"):
        raise RuntimeError(json.dumps(payload["errors"], sort_keys=True))
    repository = payload.get("data", {}).get("repository")
    if repository is None:
        raise RuntimeError("repository is not visible to the audit identity")
    return repository


def graphql_pr_page(token: str, variables: dict[str, Any]) -> dict[str, Any]:
    return graphql(token, PR_QUERY, variables)["pullRequests"]


def graphql_issue(token: str, variables: dict[str, Any]) -> dict[str, Any]:
    issue = graphql(token, ISSUE_QUERY, variables)["issue"]
    if issue is None:
        raise RuntimeError("closing Issue is not visible to the audit identity")
    return issue


def field_values(item: dict[str, Any]) -> dict[str, str]:
    values = {}
    for node in item.get("fieldValues", {}).get("nodes", []):
        field = node.get("field") or {}
        value = node.get("name") or node.get("date") or node.get("text")
        if field.get("name") and value:
            values[field["name"]] = value
    return values


def author_kind(author: dict[str, Any] | None) -> str:
    author = author or {}
    login = author.get("login") or ""
    if author.get("__typename") == "Bot" or login.endswith("[bot]") or login.startswith("app/"):
        return "automation"
    return "human" if login else "unknown"


def branch_has_issue(head: str, issue_numbers: list[int]) -> bool:
    return any(re.search(rf"(?<!\d){number}(?!\d)", head) for number in issue_numbers)


def factor_record(issue: dict[str, Any], required: list[str], completion: list[str]) -> dict[str, Any]:
    projects = []
    for item in issue.get("projectItems", {}).get("nodes", []):
        project = item.get("project") or {}
        values = field_values(item)
        missing = [name for name in required if not values.get(name)]
        if not any(values.get(name) for name in completion):
            missing.append("Target Date or Completed Date")
        projects.append({
            "owner": (project.get("owner") or {}).get("login"),
            "number": project.get("number"),
            "title": project.get("title"),
            "url": project.get("url"),
            "fields": values,
            "missingFields": missing,
            "complete": not missing,
        })
    parent = issue.get("parent")
    has_parent_or_root = bool(parent) or "Parent Issue:" in (issue.get("body") or "")
    return {
        "repository": issue["repository"]["nameWithOwner"],
        "number": issue["number"],
        "url": issue["url"],
        "issueType": (issue.get("issueType") or {}).get("name"),
        "hasParentOrRootDeclaration": has_parent_or_root,
        "parent": ({
            "repository": parent["repository"]["nameWithOwner"],
            "number": parent["number"],
            "url": parent["url"],
        } if parent else None),
        "projects": projects,
        "hasProject": bool(projects),
        "hasCompleteProject": any(project["complete"] for project in projects),
        "complete": bool(issue.get("issueType")) and has_parent_or_root and any(
            project["complete"] for project in projects
        ),
    }


def pr_record(repository: str, pr: dict[str, Any], as_of: dt.datetime, config: dict[str, Any]) -> dict[str, Any]:
    factor_connection = pr.get("closingIssuesReferences", {})
    factors = [
        factor_record(issue, config["requiredFields"], config["completionDateFields"])
        for issue in factor_connection.get("nodes", [])
    ]
    issue_numbers = [factor["number"] for factor in factors]
    created = dt.datetime.fromisoformat(pr["createdAt"].replace("Z", "+00:00"))
    detached = not factors
    return {
        "repository": repository,
        "number": pr["number"],
        "url": pr["url"],
        "title": pr["title"],
        "headRefName": pr["headRefName"],
        "createdAt": pr["createdAt"],
        "updatedAt": pr["updatedAt"],
        "ageDays": (as_of.date() - created.date()).days,
        "author": {
            "login": (pr.get("author") or {}).get("login"),
            "kind": author_kind(pr.get("author")),
        },
        "factors": factors,
        "factorCount": factor_connection.get("totalCount", len(factors)),
        "factorReferencesTruncated": factor_connection.get("totalCount", len(factors)) > len(factors),
        "detached": detached,
        "branchHasFactorIdentity": branch_has_issue(pr["headRefName"], issue_numbers),
        "hasProjectFactor": any(factor["hasProject"] for factor in factors),
        "hasCompleteProjectFactor": any(factor["hasCompleteProject"] for factor in factors),
        "completeFactorContext": any(factor["complete"] for factor in factors),
        "transactionComplete": (
            bool(factors)
            and branch_has_issue(pr["headRefName"], issue_numbers)
            and any(factor["complete"] for factor in factors)
        ),
    }


def audit_repository(
    repository: str,
    token: str,
    as_of: dt.datetime,
    config: dict[str, Any],
    fetch: Callable[[str, dict[str, Any]], dict[str, Any]] = graphql_pr_page,
    issue_fetch: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    owner, repo = repository.split("/", 1)
    cursor = None
    records = []
    issue_cache: dict[tuple[str, int], dict[str, Any]] = {}
    if issue_fetch is None and fetch is graphql_pr_page:
        issue_fetch = graphql_issue
    while True:
        page = fetch(token, {"owner": owner, "repo": repo, "cursor": cursor})
        for pr in page["nodes"]:
            if issue_fetch:
                for issue in pr.get("closingIssuesReferences", {}).get("nodes", []):
                    issue_repo = issue["repository"]["nameWithOwner"]
                    key = (issue_repo, issue["number"])
                    if key not in issue_cache:
                        issue_owner, issue_name = issue_repo.split("/", 1)
                        issue_cache[key] = issue_fetch(token, {
                            "owner": issue_owner,
                            "repo": issue_name,
                            "number": issue["number"],
                        })
                    issue["projectItems"] = issue_cache[key]["projectItems"]
            records.append(pr_record(repository, pr, as_of, config))
        if not page["pageInfo"]["hasNextPage"]:
            return records
        cursor = page["pageInfo"]["endCursor"]


def build_ledger(
    repositories: list[str],
    token: str,
    as_of: dt.datetime,
    config: dict[str, Any],
    workers: int,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(audit_repository, repository, token, as_of, config): repository
            for repository in repositories
        }
        for future in concurrent.futures.as_completed(futures):
            repository = futures[future]
            try:
                records.extend(future.result())
            except Exception as error:  # preserve a reviewable partial result
                errors.append({"repository": repository, "error": str(error)})
    records.sort(key=lambda item: (item["repository"].casefold(), item["number"]))
    detached = [record for record in records if record["detached"]]
    return {
        "apiVersion": "governance.re8ch.com/v1alpha1",
        "kind": "OpenPullRequestTransactionLedger",
        "generatedAt": as_of.isoformat().replace("+00:00", "Z"),
        "source": "operations/github-transaction-context/repository-inventory.json",
        "summary": {
            "repositoryCount": len(repositories),
            "repositoryErrors": len(errors),
            "openPullRequests": len(records),
            "linkedPullRequests": len(records) - len(detached),
            "detachedPullRequests": len(detached),
            "branchIdentityMissing": sum(not item["branchHasFactorIdentity"] for item in records if not item["detached"]),
            "projectFactorMissing": sum(not item["hasProjectFactor"] for item in records if not item["detached"]),
            "completeProjectFactorMissing": sum(not item["hasCompleteProjectFactor"] for item in records if not item["detached"]),
            "transactionComplete": sum(item["transactionComplete"] for item in records),
            "transactionIncomplete": sum(not item["transactionComplete"] for item in records),
            "humanPullRequests": sum(item["author"]["kind"] == "human" for item in records),
            "automationPullRequests": sum(item["author"]["kind"] == "automation" for item in records),
        },
        "errors": sorted(errors, key=lambda item: item["repository"].casefold()),
        "pullRequests": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--as-of", help="ISO-8601 timestamp; defaults to current UTC time")
    args = parser.parse_args()
    as_of = (
        dt.datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
        if args.as_of else dt.datetime.now(dt.timezone.utc)
    )
    inventory = json.loads(args.inventory.read_text())
    repositories = [item["repository"] for item in inventory["repositories"]]
    ledger = build_ledger(
        repositories, os.environ["GH_TOKEN"], as_of,
        json.loads(args.config.read_text()), args.workers,
    )
    output = json.dumps(ledger, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(output)
    else:
        sys.stdout.write(output)
    return bool(ledger["errors"])


if __name__ == "__main__":
    raise SystemExit(main())
