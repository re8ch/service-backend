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
query($owner: String!, $repo: String!, $number: Int!, $project: Int!) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) {
      id number title body issueType { name }
      comments(last: 50) { nodes { body } }
      projectItems(first: 20) { nodes { id project { id number } } }
    }
    issueTypes(first: 20) { nodes { id name } }
  }
  organization(login: $owner) {
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


def reconcile(token: str, repository: str, number: int, today: dt.date) -> None:
    owner, repo = repository.split("/", 1)
    data = request(token, QUERY, {"owner": owner, "repo": repo, "number": number, "project": PROJECT_NUMBER})
    issue = data["repository"]["issue"]
    project = data["organization"]["projectV2"]
    if not issue or not project:
        raise RuntimeError("Issue or approved Project was not found")

    issue_types = {node["name"]: node["id"] for node in data["repository"]["issueTypes"]["nodes"]}
    selected_type = native_issue_type(work_type(issue.get("body") or ""))
    if (issue.get("issueType") or {}).get("name") != selected_type:
        mutation(token, "mutation($id:ID!,$type:ID!){updateIssue(input:{id:$id,issueTypeId:$type}){issue{id}}}", id=issue["id"], type=issue_types[selected_type])

    item = next((node for node in issue["projectItems"]["nodes"] if node["project"]["number"] == PROJECT_NUMBER), None)
    if not item:
        result = mutation(token, "mutation($project:ID!,$content:ID!){addProjectV2ItemById(input:{projectId:$project,contentId:$content}){item{id}}}", project=project["id"], content=issue["id"])
        item = result["addProjectV2ItemById"]["item"]

    fields = {node["name"]: node for node in project["fields"]["nodes"] if node.get("name")}
    for name, value in desired_fields(issue.get("body") or "", today).items():
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--issue", required=True, type=int)
    args = parser.parse_args()
    reconcile(os.environ["GH_TOKEN"], args.repository, args.issue, dt.date.today())
    print(f"Issue #{args.issue} transaction defaults reconciled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
