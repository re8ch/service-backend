#!/usr/bin/env python3
"""Apply deterministic transaction defaults when a governed Issue is opened."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import urllib.request
from typing import Any


MARKER = "<!-- transaction-context-intake:v1 -->"
PROJECT_NUMBER = 2
FIELD_DEFAULTS = {
    "Status": "Todo",
    "Lifecycle": "Ready",
    "Context Status": "Ready",
}

QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $projectOwner: String!, $project: Int!) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) {
      id number title body issueType { name }
      comments(last: 50) { nodes { body } }
      projectItems(first: 20) {
        nodes {
          id
          project { id number owner { ... on Organization { login } ... on User { login } } }
          fieldValues(first: 50) {
            nodes {
              ... on ProjectV2ItemFieldSingleSelectValue { name field { ... on ProjectV2SingleSelectField { name } } }
              ... on ProjectV2ItemFieldDateValue { date field { ... on ProjectV2FieldCommon { name } } }
            }
          }
        }
      }
    }
    issueTypes(first: 20) { nodes { id name } }
  }
  organization(login: $projectOwner) {
    projectV2(number: $project) {
      id
      fields(first: 50) {
        nodes {
          ... on ProjectV2Field { id name dataType }
          ... on ProjectV2SingleSelectField { id name options { id name } }
        }
      }
    }
  }
}
"""

SCAN_QUERY = """
query($owner: String!, $repo: String!) {
  repository(owner: $owner, name: $repo) {
    issues(first: 30, states: OPEN, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes { number comments(last: 20) { nodes { body } } }
    }
  }
}
"""


def issue_form_value(body: str, heading: str) -> str | None:
    pattern = rf"(?ms)^### {re.escape(heading)}\s*$\s*(.+?)(?=^### |\Z)"
    match = re.search(pattern, body)
    if not match:
        return None
    value = match.group(1).strip()
    return value if value and value != "_No response_" else None


def work_type(body: str) -> str:
    selected = issue_form_value(body, "Work type") or "Task"
    supported = {
        "Feature", "Bug", "Task", "Governance", "Dependency",
        "Documentation", "Migration",
    }
    return selected if selected in supported else "Task"


def native_issue_type(selected: str) -> str:
    return selected if selected in {"Feature", "Bug"} else "Task"


def branch_name(number: int, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")[:48].rstrip("-")
    return f"codex/{number}-{slug or 'work-item'}"


def desired_fields(body: str, today: dt.date) -> dict[str, str]:
    return {
        **FIELD_DEFAULTS,
        "Work Type": work_type(body),
        "Start Date": today.isoformat(),
        "Target Date": (today + dt.timedelta(days=14)).isoformat(),
    }


def next_action(issue: dict[str, Any]) -> str:
    branch = branch_name(issue["number"], issue["title"])
    return f"""{MARKER}
上下文已自动准备完成。下一步只需从此 Issue 开发：

```text
branch: {branch}
PR: Closes #{issue['number']}
```

Project、类型、生命周期和日期默认值已补齐；仅当业务归属或目标日期不正确时再修改 Project 项。验收时补充运行证据即可。
"""


def request(token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "re8ch-transaction-context-intake",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.load(response)
    if payload.get("errors"):
        raise RuntimeError(json.dumps(payload["errors"], indent=2))
    return payload["data"]


def mutation(token: str, query: str, **variables: Any) -> dict[str, Any]:
    return request(token, query, variables)


def current_fields(item: dict[str, Any]) -> dict[str, str]:
    values = {}
    for node in item.get("fieldValues", {}).get("nodes", []):
        name = (node.get("field") or {}).get("name")
        value = node.get("name") or node.get("date")
        if name and value:
            values[name] = value
    return values


def reconcile(
    token: str,
    repository: str,
    number: int,
    today: dt.date,
    project_owner: str | None = None,
    project_number: int = PROJECT_NUMBER,
) -> None:
    owner, repo = repository.split("/", 1)
    project_owner = project_owner or owner
    data = request(token, QUERY, {
        "owner": owner, "repo": repo, "number": number,
        "projectOwner": project_owner, "project": project_number,
    })
    issue = data["repository"]["issue"]
    project = data["organization"]["projectV2"]
    if not issue or not project:
        raise RuntimeError("Issue or approved Project was not found")

    issue_types = {node["name"]: node["id"] for node in data["repository"]["issueTypes"]["nodes"]}
    selected_type = native_issue_type(work_type(issue.get("body") or ""))
    if (issue.get("issueType") or {}).get("name") != selected_type:
        mutation(token, "mutation($id:ID!,$type:ID!){updateIssue(input:{id:$id,issueTypeId:$type}){issue{id}}}", id=issue["id"], type=issue_types[selected_type])

    item = next((
        node for node in issue["projectItems"]["nodes"]
        if node["project"]["number"] == project_number
        and (node["project"].get("owner") or {}).get("login") == project_owner
    ), None)
    if not item:
        result = mutation(token, "mutation($project:ID!,$content:ID!){addProjectV2ItemById(input:{projectId:$project,contentId:$content}){item{id}}}", project=project["id"], content=issue["id"])
        item = result["addProjectV2ItemById"]["item"]

    existing = current_fields(item)
    fields = {node["name"]: node for node in project["fields"]["nodes"] if node.get("name")}
    for name, value in desired_fields(issue.get("body") or "", today).items():
        if existing.get(name):
            continue
        field = fields[name]
        if "options" in field:
            options = {option["name"]: option["id"] for option in field["options"]}
            field_value = {"singleSelectOptionId": options[value]}
        else:
            field_value = {"date": value}
        mutation(token, "mutation($project:ID!,$item:ID!,$field:ID!,$value:ProjectV2FieldValue!){updateProjectV2ItemFieldValue(input:{projectId:$project,itemId:$item,fieldId:$field,value:$value}){projectV2Item{id}}}", project=project["id"], item=item["id"], field=field["id"], value=field_value)

    already_commented = any(MARKER in node.get("body", "") for node in issue["comments"]["nodes"])
    if not already_commented:
        mutation(token, "mutation($subject:ID!,$body:String!){addComment(input:{subjectId:$subject,body:$body}){commentEdge{node{id}}}}", subject=issue["id"], body=next_action(issue))


def scan(
    token: str,
    repository: str,
    today: dt.date,
    project_owner: str,
    project_number: int,
) -> list[int]:
    owner, repo = repository.split("/", 1)
    data = request(token, SCAN_QUERY, {"owner": owner, "repo": repo})
    pending = [
        node["number"]
        for node in data["repository"]["issues"]["nodes"]
        if not any(MARKER in comment.get("body", "") for comment in node["comments"]["nodes"])
    ]
    for number in pending:
        reconcile(token, repository, number, today, project_owner, project_number)
    return pending


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--issue", type=int)
    target.add_argument("--scan", action="store_true")
    parser.add_argument("--project-owner")
    parser.add_argument("--project-number", type=int, default=PROJECT_NUMBER)
    args = parser.parse_args()
    if args.scan:
        owner = args.project_owner or args.repository.split("/", 1)[0]
        reconciled = scan(
            os.environ["GH_TOKEN"], args.repository, dt.date.today(),
            owner, args.project_number,
        )
        print(f"Reconciled {len(reconciled)} Issue(s): {reconciled}")
    else:
        reconcile(
            os.environ["GH_TOKEN"], args.repository, args.issue, dt.date.today(),
            args.project_owner, args.project_number,
        )
        print(f"Issue #{args.issue} transaction defaults reconciled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
