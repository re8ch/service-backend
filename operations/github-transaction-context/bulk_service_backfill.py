#!/usr/bin/env python3
"""Bulk-link detached service-backend PRs to complete primary factor Issues."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from classify_automation_factor import classification


REPOSITORY = "re8ch/service-backend"
PROJECT_OWNER = "re8ch"
PROJECT_NUMBER = 2
PARENT_NUMBER = 3128
MARKER = "transaction-factor-backfill:service-backend:3128"


def api(token: str, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    request = urllib.request.Request(
        f"https://api.github.com/{path.lstrip('/')}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "re8ch-bulk-factor-backfill",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = response.read()
    return json.loads(payload) if payload else None


def graphql(token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    payload = api(token, "POST", "graphql", {"query": query, "variables": variables})
    if payload.get("errors"):
        raise RuntimeError(json.dumps(payload["errors"], sort_keys=True))
    return payload["data"]


def list_open_prs(token: str) -> list[dict[str, Any]]:
    rows = []
    for page in range(1, 20):
        batch = api(token, "GET", f"repos/{REPOSITORY}/pulls?state=open&per_page=100&page={page}")
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise RuntimeError("open PR pagination exceeded safety bound")


def closing_issue_numbers(token: str, number: int) -> list[int]:
    data = graphql(token, """
      query($number:Int!){repository(owner:"re8ch",name:"service-backend"){
        pullRequest(number:$number){closingIssuesReferences(first:20){nodes{number}}}
      }}
    """, {"number": number})
    return [node["number"] for node in data["repository"]["pullRequest"]["closingIssuesReferences"]["nodes"]]


def context(token: str) -> dict[str, Any]:
    data = graphql(token, """
      query { repository(owner:"re8ch",name:"service-backend") {
        issue(number:3128){id}
        issueTypes(first:20){nodes{id name}}
      }
      organization(login:"re8ch") { projectV2(number:2) {
        id fields(first:50){nodes{
          ... on ProjectV2Field{id name dataType}
          ... on ProjectV2SingleSelectField{id name options{id name}}
        }}
      }} }
    """, {})
    return data


def plan(prs: list[dict[str, Any]], linked: dict[int, list[int]], policy: dict[str, Any]) -> dict[str, Any]:
    human = []
    automation: dict[str, list[dict[str, Any]]] = {}
    exceptions = []
    already_linked = []
    for pr in prs:
        number = pr["number"]
        if linked[number]:
            already_linked.append(number)
            continue
        login = pr["user"]["login"]
        if login == "Woomou":
            human.append(pr)
            continue
        row = {
            "repository": REPOSITORY,
            "number": number,
            "author": f"app/{login.removesuffix('[bot]')}",
            "headRefName": pr["head"]["ref"],
            "title": pr["title"],
        }
        result = classification(row, policy)
        if result["decision"] == "aggregate":
            automation.setdefault(result["primaryFactorKey"], []).append(pr)
        else:
            exceptions.append({"number": number, "reason": result["reason"]})
    return {
        "human": human,
        "automation": automation,
        "exceptions": exceptions,
        "alreadyLinked": already_linked,
    }


def inferred_type(title: str) -> tuple[str, str]:
    if re.match(r"^fix(?:\(|:)", title, re.I):
        return "Bug", "Bug"
    if re.match(r"^feat(?:\(|:)", title, re.I):
        return "Feature", "Feature"
    return "Task", "Task"


def create_factor(
    token: str,
    ctx: dict[str, Any],
    title: str,
    body: str,
    native_type: str,
    work_type: str,
) -> dict[str, Any]:
    issue = api(token, "POST", f"repos/{REPOSITORY}/issues", {"title": title[:250], "body": body})
    issue_id = issue["node_id"]
    issue_types = {node["name"]: node["id"] for node in ctx["repository"]["issueTypes"]["nodes"]}
    graphql(token, "mutation($id:ID!,$type:ID!){updateIssue(input:{id:$id,issueTypeId:$type}){issue{id}}}", {"id": issue_id, "type": issue_types[native_type]})
    graphql(token, "mutation($parent:ID!,$child:ID!){addSubIssue(input:{issueId:$parent,subIssueId:$child}){issue{id}}}", {"parent": ctx["repository"]["issue"]["id"], "child": issue_id})

    project = ctx["organization"]["projectV2"]
    try:
        added = graphql(token, "mutation($project:ID!,$content:ID!){addProjectV2ItemById(input:{projectId:$project,contentId:$content}){item{id}}}", {"project": project["id"], "content": issue_id})
        item_id = added["addProjectV2ItemById"]["item"]["id"]
    except RuntimeError as error:
        if "Content already exists in this project" not in str(error):
            raise
        existing = graphql(token, "query($id:ID!){node(id:$id){... on Issue{projectItems(first:20){nodes{id project{id}}}}}}", {"id": issue_id})
        item_id = next(
            node["id"] for node in existing["node"]["projectItems"]["nodes"]
            if node["project"]["id"] == project["id"]
        )
    fields = {node["name"]: node for node in project["fields"]["nodes"] if node.get("name")}
    today = dt.date.today()
    values = {
        "Status": "Todo",
        "Lifecycle": "Backlog",
        "Work Type": work_type,
        "Context Status": "Ready",
        "Start Date": today.isoformat(),
        "Target Date": (today + dt.timedelta(days=30)).isoformat(),
    }
    for name, value in values.items():
        field = fields[name]
        if "options" in field:
            options = {option["name"]: option["id"] for option in field["options"]}
            encoded = {"singleSelectOptionId": options[value]}
        else:
            encoded = {"date": value}
        graphql(token, "mutation($project:ID!,$item:ID!,$field:ID!,$value:ProjectV2FieldValue!){updateProjectV2ItemFieldValue(input:{projectId:$project,itemId:$item,fieldId:$field,value:$value}){projectV2Item{id}}}", {"project": project["id"], "item": item_id, "field": field["id"], "value": encoded})
    return {"number": issue["number"], "url": issue["html_url"], "id": issue_id}


def append_closing_link(token: str, pr: dict[str, Any], factor: int) -> None:
    suffix = f"<!-- {MARKER} -->\nCloses #{factor}"
    body = pr.get("body") or ""
    if suffix in body:
        return
    api(token, "PATCH", f"repos/{REPOSITORY}/pulls/{pr['number']}", {"body": f"{body.rstrip()}\n\n{suffix}\n"})


def execute(token: str, planned: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    ctx = context(token)
    transactions = []
    failures = []

    for key, prs in sorted(planned["automation"].items()):
        try:
            actor = "Dependabot" if key.startswith("dependency-update:") else "domain promoter"
            factor = create_factor(
                token,
                ctx,
                f"Maintain {key.split(':')[-1]} automation stream",
                "\n".join([
                    "## Outcome",
                    f"Own the lifecycle and review of the `{key}` {actor} update stream.",
                    "",
                    "## Context",
                    "- Parent Issue: #3128",
                    "- Project: Service Backend Internal Operations",
                    f"- Aggregation key: `{key}`",
                    "",
                    "## Pull requests",
                    *[f"- {pr['html_url']}" for pr in prs],
                    "",
                    f"<!-- {MARKER}:factor:{key} -->",
                ]),
                "Task",
                "Dependency",
            )
            for pr in prs:
                append_closing_link(token, pr, factor["number"])
                transactions.append({"pr": pr["number"], "factor": factor["number"], "kind": "automation", "key": key})
        except Exception as error:
            failures.append({"key": key, "prs": [pr["number"] for pr in prs], "error": str(error)})

    def process_human(pr: dict[str, Any]) -> dict[str, Any]:
        native_type, work_type = inferred_type(pr["title"])
        factor = create_factor(
            token,
            ctx,
            f"Factor PR #{pr['number']}: {pr['title']}",
            "\n".join([
                "## Outcome",
                f"Own lifecycle, review and acceptance for {pr['html_url']}.",
                "",
                "## Context",
                "- Parent Issue: #3128",
                "- Project: Service Backend Internal Operations",
                f"- Source PR: {pr['html_url']}",
                "",
                "## Acceptance",
                "- [ ] Review the retained historical change against current main",
                "- [ ] Record checks and runtime evidence when applicable",
                "- [ ] Merge or close the PR through a separately reviewed decision",
                "",
                f"<!-- {MARKER}:factor:pr-{pr['number']} -->",
            ]),
            native_type,
            work_type,
        )
        append_closing_link(token, pr, factor["number"])
        return {"pr": pr["number"], "factor": factor["number"], "kind": "human"}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(process_human, pr): pr for pr in planned["human"]}
        for future in concurrent.futures.as_completed(futures):
            pr = futures[future]
            try:
                transactions.append(future.result())
            except Exception as error:
                failures.append({"pr": pr["number"], "error": str(error)})
    return {"transactions": transactions, "failures": failures}


def verify(token: str, result: dict[str, Any]) -> dict[str, Any]:
    verified, failed = [], []
    open_prs = {pr["number"] for pr in list_open_prs(token)}
    def readback(transaction: dict[str, Any]) -> tuple[dict[str, Any], list[int]]:
        factors = closing_issue_numbers(token, transaction["pr"])
        return transaction, factors

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(readback, transaction) for transaction in result["transactions"]]
        readbacks = [future.result() for future in concurrent.futures.as_completed(futures)]
    for transaction, factors in readbacks:
        if transaction["factor"] in factors and transaction["pr"] in open_prs:
            verified.append(transaction)
        else:
            failed.append({**transaction, "closingIssues": factors, "stillOpen": transaction["pr"] in open_prs})
    return {"verified": verified, "failed": failed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--policy", type=Path, default=Path(__file__).with_name("automation-factor-policy.json"))
    args = parser.parse_args()
    token = os.environ["GH_TOKEN"]
    policy = json.loads(args.policy.read_text())
    prs = list_open_prs(token)
    linked = {pr["number"]: closing_issue_numbers(token, pr["number"]) for pr in prs}
    planned = plan(prs, linked, policy)
    automation_candidates = {
        key: len(value) for key, value in sorted(planned["automation"].items())
    }
    # Automation is migrated by the fleet-wide automation wave. Keeping this
    # concern human-only prevents two writers from assigning competing factors.
    planned["automation"] = {}
    summary: dict[str, Any] = {
        "apiVersion": "governance.re8ch.com/v1alpha1",
        "kind": "PullRequestFactorBackfill",
        "repository": REPOSITORY,
        "parentIssue": f"{REPOSITORY}#{PARENT_NUMBER}",
        "capturedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "before": {
            "open": len(prs),
            "alreadyLinked": len(planned["alreadyLinked"]),
            "detachedHuman": len(planned["human"]),
            "automationStreamsSkippedForFleetWave": automation_candidates,
            "exceptions": planned["exceptions"],
        },
        "mode": "apply" if args.apply else "plan",
    }
    if args.apply:
        result = execute(token, planned, policy)
        readback = verify(token, result)
        summary["result"] = {
            "attempted": len(result["transactions"]) + len(result["failures"]),
            "mutated": len(result["transactions"]),
            "mutationFailures": result["failures"],
            "verified": len(readback["verified"]),
            "readbackFailures": readback["failed"],
            "transactions": readback["verified"],
        }
    rendered = json.dumps(summary, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end="")
    failures = summary.get("result", {}).get("mutationFailures", [])
    readback_failures = summary.get("result", {}).get("readbackFailures", [])
    return bool(failures or readback_failures)


if __name__ == "__main__":
    raise SystemExit(main())
