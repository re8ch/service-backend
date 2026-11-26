#!/usr/bin/env python3
"""Fail a pull request that is detached from its Issue and Project context."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any


QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      number
      headRefName
      author { login }
      closingIssuesReferences(first: 20) {
        nodes {
          number
          body
          issueType { name }
          parent { number }
          projectItems(first: 20) {
            nodes {
              project {
                number
                title
                owner {
                  ... on Organization { login }
                  ... on User { login }
                }
              }
              fieldValues(first: 50) {
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
  }
}
"""


def field_values(item: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for node in item.get("fieldValues", {}).get("nodes", []):
        field = node.get("field") or {}
        name = field.get("name")
        value = node.get("name") or node.get("date") or node.get("text")
        if name and value:
            values[name.casefold()] = value
    return values


def exemption(pr: dict[str, Any], config: dict[str, Any]) -> str | None:
    author = (pr.get("author") or {}).get("login")
    head = pr.get("headRefName", "")
    for rule in config.get("exemptions", []):
        if author == rule["author"] and head.startswith(rule["headPrefix"]):
            return rule["reason"]
    return None


def validate(pr: dict[str, Any], config: dict[str, Any]) -> list[str]:
    exempt_reason = exemption(pr, config)
    if exempt_reason:
        print(f"Explicit exemption: {exempt_reason}")
        return []

    issues = pr.get("closingIssuesReferences", {}).get("nodes", [])
    if not issues:
        return ["PR must close at least one Issue with Fixes/Closes/Resolves."]

    failures: list[str] = []
    organization = config["organization"]
    approved = set(config["approvedProjectNumbers"])
    required = config["requiredFields"]
    completion = config["completionDateFields"]
    head = pr.get("headRefName", "")

    for issue in issues:
        number = issue["number"]
        prefix = f"Issue #{number}"
        if str(number) not in head:
            failures.append(f"{prefix}: branch name must contain the Issue number.")
        if not issue.get("issueType"):
            failures.append(f"{prefix}: Issue Type must be set.")

        body = issue.get("body") or ""
        if "Parent Issue:" not in body and not issue.get("parent"):
            failures.append(f"{prefix}: declare a parent Issue or `Parent Issue: none`.")

        items = [
            item
            for item in issue.get("projectItems", {}).get("nodes", [])
            if (item.get("project", {}).get("owner") or {}).get("login") == organization
            and item.get("project", {}).get("number") in approved
        ]
        if not items:
            failures.append(f"{prefix}: add the Issue to an approved {organization} Project.")
            continue

        complete_item = False
        item_failures: list[str] = []
        for item in items:
            values = field_values(item)
            missing = [name for name in required if not values.get(name.casefold())]
            if not any(values.get(name.casefold()) for name in completion):
                missing.append("Target Date or Completed Date")
            if not missing:
                complete_item = True
                break
            project = item["project"]
            item_failures.append(
                f"{project['title']} (#{project['number']}): missing {', '.join(missing)}"
            )
        if not complete_item:
            failures.append(f"{prefix}: " + "; ".join(item_failures))

    return failures


def graphql(token: str, variables: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": variables}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "re8ch-transaction-context",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get("errors"):
        raise RuntimeError(json.dumps(payload["errors"], indent=2))
    return payload["data"]["repository"]["pullRequest"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pull-request", required=True, type=int)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("config.json"),
    )
    args = parser.parse_args()
    owner, repo = args.repository.split("/", 1)
    config = json.loads(args.config.read_text())
    pr = graphql(
        os.environ["GH_TOKEN"],
        {"owner": owner, "repo": repo, "number": args.pull_request},
    )
    failures = validate(pr, config)
    if failures:
        print("Transaction context is incomplete:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(f"PR #{pr['number']} has complete Issue and Project transaction context.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
