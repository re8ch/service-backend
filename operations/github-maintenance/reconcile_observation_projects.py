#!/usr/bin/env python3
"""Reconcile repository-linked organization Projects for bounded GitOps evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_DECLARATION = Path(__file__).with_name("gitops-observation-projects.json")


def gh_json(args: list[str], *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    command = ["gh", "api", *args]
    if payload is not None:
        command.extend(["--input", "-"])
    result = subprocess.run(
        command,
        input=json.dumps(payload) if payload is not None else None,
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    response = gh_json(["graphql"], payload={"query": query, "variables": variables})
    if response.get("errors"):
        raise RuntimeError(json.dumps(response["errors"], sort_keys=True))
    return response["data"]


def owner_id(owner: str) -> str:
    return str(gh_json([f"orgs/{owner}"])["node_id"])


def repository_id(repository: str) -> str:
    return str(gh_json([f"repos/{repository}"])["node_id"])


def projects(owner: str) -> list[dict[str, Any]]:
    query = """
      query($login: String!) {
        organization(login: $login) {
          projectsV2(first: 100) { nodes { id number title url closed repositories(first: 20) { nodes { nameWithOwner } } } }
        }
      }
    """
    return graphql(query, {"login": owner})["organization"]["projectsV2"]["nodes"]


def project_fields(project_id: str) -> list[dict[str, Any]]:
    query = """
      query($id: ID!) {
        node(id: $id) {
          ... on ProjectV2 {
            fields(first: 100) {
              nodes {
                __typename
                ... on ProjectV2Field { id name dataType }
                ... on ProjectV2SingleSelectField { id name options { name } }
              }
            }
          }
        }
      }
    """
    return graphql(query, {"id": project_id})["node"]["fields"]["nodes"]


def create_project(spec: dict[str, Any]) -> dict[str, Any]:
    mutation = """
      mutation($owner: ID!, $repository: ID!, $title: String!) {
        createProjectV2(input: {ownerId: $owner, repositoryId: $repository, title: $title}) {
          projectV2 { id number title url }
        }
      }
    """
    return graphql(mutation, {"owner": owner_id(spec["owner"]), "repository": repository_id(spec["repository"]), "title": spec["title"]})["createProjectV2"]["projectV2"]


def update_project(project_id: str, spec: dict[str, Any]) -> None:
    mutation = """
      mutation($id: ID!, $description: String!, $readme: String!) {
        updateProjectV2(input: {projectId: $id, public: false, shortDescription: $description, readme: $readme}) { projectV2 { id } }
      }
    """
    readme = (
        "## Scope\n\n"
        f"This Project is the repository-specific observation view for `{spec['repository']}`. "
        "Store bounded results, actions, revisions, run identifiers and links to redacted evidence. "
        "Raw telemetry and credentials remain outside GitHub.\n\n"
        "The `RE8CH GitOps Maintenance` Project is a cross-repository summary and escalation view only."
    )
    graphql(mutation, {"id": project_id, "description": spec["shortDescription"], "readme": readme})


def link_project(project_id: str, repository: str) -> None:
    mutation = """
      mutation($project: ID!, $repository: ID!) {
        linkProjectV2ToRepository(input: {projectId: $project, repositoryId: $repository}) { repository { nameWithOwner } }
      }
    """
    graphql(mutation, {"project": project_id, "repository": repository_id(repository)})


def create_field(project_id: str, field: dict[str, Any]) -> None:
    mutation = """
      mutation($input: CreateProjectV2FieldInput!) {
        createProjectV2Field(input: $input) {
          projectV2Field { ... on ProjectV2Field { id name } ... on ProjectV2SingleSelectField { id name } }
        }
      }
    """
    value: dict[str, Any] = {"projectId": project_id, "dataType": field["dataType"], "name": field["name"]}
    if field["dataType"] == "SINGLE_SELECT":
        value["singleSelectOptions"] = [{"name": name, "color": "GRAY", "description": ""} for name in field["options"]]
    graphql(mutation, {"input": value})


def validate_declaration(document: dict[str, Any]) -> None:
    if document.get("kind") != "GitOpsObservationProjectSet":
        raise ValueError("unexpected declaration kind")
    seen: set[tuple[str, str]] = set()
    for spec in document.get("projects", []):
        key = (spec["owner"], spec["title"])
        if key in seen:
            raise ValueError(f"duplicate project: {key}")
        seen.add(key)
        if spec["repository"].split("/", 1)[0] != spec["owner"]:
            raise ValueError(f"project and repository owner differ: {spec['repository']}")


def reconcile(document: dict[str, Any], *, apply: bool) -> list[dict[str, Any]]:
    validate_declaration(document)
    results: list[dict[str, Any]] = []
    for spec in document["projects"]:
        existing = next((item for item in projects(spec["owner"]) if item["title"] == spec["title"]), None)
        if not apply:
            results.append({"repository": spec["repository"], "project": spec["title"], "action": "update" if existing else "create"})
            continue
        project = existing or create_project(spec)
        project_id = project["id"]
        update_project(project_id, spec)
        linked = {node["nameWithOwner"] for node in project.get("repositories", {}).get("nodes", [])}
        if existing and spec["repository"] not in linked:
            link_project(project_id, spec["repository"])
        existing_fields = {field.get("name"): field for field in project_fields(project_id)}
        for field in document["fields"]:
            if field["name"] not in existing_fields:
                create_field(project_id, field)
        results.append({"repository": spec["repository"], "project": spec["title"], "number": project["number"], "url": project["url"], "action": "updated" if existing else "created"})
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--declaration", type=Path, default=DEFAULT_DECLARATION)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    document = json.loads(args.declaration.read_text())
    print(json.dumps({"applied": args.apply, "projects": reconcile(document, apply=args.apply)}, indent=2))


if __name__ == "__main__":
    main()
