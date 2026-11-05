#!/usr/bin/env python3
"""Validate and render the RE8CH GitHub-native developer context graph."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
DECLARATION = ROOT / "workspace/developer-context.yaml"


class ContextError(RuntimeError):
    pass


def load(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text())
    if not isinstance(value, dict):
        raise ContextError(f"{path}: expected a mapping")
    return value


def repository_key(url: str) -> str:
    value = url.removesuffix(".git").replace("git@github.com:", "github.com/")
    value = value.replace("ssh://git@ssh.github.com:443/", "github.com/")
    return value.removeprefix("https://").lower()


def module_documents() -> list[tuple[Path, dict[str, Any]]]:
    return [(path, load(path)) for path in sorted(ROOT.glob("catalogs/*/modules/*/module.yaml"))]


def graph() -> dict[str, Any]:
    declaration = load(DECLARATION)
    federation = load(ROOT / declaration["spec"]["sources"]["gitops"])
    modules = module_documents()
    names = {doc["metadata"]["name"] for _, doc in modules}
    nodes: list[dict[str, Any]] = [{
        "id": "service-backend-context",
        "kind": "context-authority",
        "repository": declaration["spec"]["authority"]["repository"],
    }]
    edges: list[dict[str, str]] = []

    for path, doc in modules:
        metadata, spec = doc["metadata"], doc["spec"]
        name = metadata["name"]
        nodes.append({
            "id": name,
            "kind": "module",
            "catalog": metadata["catalog"],
            "projectType": spec["projectType"],
            "repository": spec["repository"]["url"],
            "declaration": str(path.relative_to(ROOT)),
            "context": [
                "AGENTS.md",
                "catalogs/_shared/backend-platform-context.md",
                str(path.parent.relative_to(ROOT) / "MEMORY.md"),
            ],
        })
        edges.append({"from": name, "to": "service-backend-context", "type": "context-provided-by"})
        for dependency in spec.get("dependencies", []):
            if dependency not in names:
                raise ContextError(f"{name}: unknown dependency {dependency}")
            edges.append({"from": name, "to": dependency, "type": "depends-on"})

    for domain, spec in sorted(federation["spec"]["domains"].items()):
        node_id = f"gitops:{domain}"
        nodes.append({
            "id": node_id,
            "kind": "gitops-repository",
            "domain": domain,
            "repository": spec["repository"],
            "controllerNamespace": spec["controllerNamespace"],
            "declaration": declaration["spec"]["sources"]["gitops"],
        })
        edges.append({"from": node_id, "to": "service-backend-context", "type": "governed-by"})

    surfaces = declaration["spec"]["surfaces"]
    nodes.append({"id": "github:wiki", "kind": "collaboration-surface", **surfaces["wiki"]})
    nodes.append({"id": "github:gists", "kind": "collaboration-surface", **surfaces["gists"]})
    for project in surfaces["projects"]:
        nodes.append({"id": f"github:project:{project['organization']}:{project['number']}", "kind": "collaboration-surface", **project})
    for node in nodes:
        if node["kind"] == "collaboration-surface":
            edges.append({"from": node["id"], "to": "service-backend-context", "type": "projects"})

    return {
        "apiVersion": "workspace.re8ch.com/v1alpha1",
        "kind": "DeveloperContextGraph",
        "sourceSha256": hashlib.sha256(DECLARATION.read_bytes()).hexdigest(),
        "nodes": sorted(nodes, key=lambda item: item["id"]),
        "edges": sorted(edges, key=lambda item: (item["from"], item["type"], item["to"])),
    }


def validate() -> dict[str, Any]:
    declaration = load(DECLARATION)
    if declaration.get("apiVersion") != "workspace.re8ch.com/v1alpha1" or declaration.get("kind") != "DeveloperContext":
        raise ContextError("workspace/developer-context.yaml has an unsupported type")
    if declaration["spec"]["authority"]["repository"] != "https://github.com/re8ch/service-backend.git":
        raise ContextError("service-backend must remain the developer-context authority")
    if any(project.get("authority") is not False for project in declaration["spec"]["surfaces"]["projects"]):
        raise ContextError("GitHub Projects cannot be desired-state authorities")
    result = graph()
    repository_keys = [repository_key(node["repository"]) for node in result["nodes"] if node.get("repository") and node["kind"] in {"module", "gitops-repository"}]
    duplicates = sorted({key for key in repository_keys if repository_keys.count(key) > 1})
    if duplicates:
        raise ContextError(f"repositories appear more than once: {duplicates}")
    return {"healthy": True, "nodes": len(result["nodes"]), "edges": len(result["edges"])}


def write_index(check: bool) -> dict[str, Any]:
    rendered = json.dumps(graph(), indent=2, sort_keys=True) + "\n"
    path = ROOT / "indexes/developer-context.json"
    if check:
        if not path.exists() or path.read_text() != rendered:
            raise ContextError(f"{path.relative_to(ROOT)} is stale; run context.py index")
    else:
        path.write_text(rendered)
    return {"path": str(path.relative_to(ROOT)), "checked": check}


def wiki_pages() -> dict[str, str]:
    rendered = graph()
    modules = [node for node in rendered["nodes"] if node["kind"] == "module"]
    gitops = [node for node in rendered["nodes"] if node["kind"] == "gitops-repository"]
    catalogs: dict[str, list[dict[str, Any]]] = {}
    for module in modules:
        catalogs.setdefault(module["catalog"], []).append(module)
    home = [
        "# RE8CH developer context",
        "",
        "> Generated from `workspace/developer-context.yaml`; Git `main` remains authoritative.",
        "",
        f"This view covers {len(modules)} catalog repositories and {len(gitops)} autonomous GitOps repositories.",
        "",
        "- [Repository context](Repository-Context)",
        "- [GitOps context](GitOps-Context)",
        "- Source: `workspace/developer-context.yaml`",
        "",
    ]
    repository_page = ["# Repository context", ""]
    for catalog, items in sorted(catalogs.items()):
        repository_page.extend([f"## {catalog}", "", "| Module | Type | Repository | Direct dependencies |", "| --- | --- | --- | --- |"])
        dependencies = {edge["from"]: [] for edge in rendered["edges"] if edge["type"] == "depends-on"}
        for edge in rendered["edges"]:
            if edge["type"] == "depends-on":
                dependencies.setdefault(edge["from"], []).append(edge["to"])
        for item in sorted(items, key=lambda value: value["id"]):
            deps = ", ".join(sorted(dependencies.get(item["id"], []))) or "—"
            repository_page.append(f"| `{item['id']}` | `{item['projectType']}` | {item['repository']} | {deps} |")
        repository_page.append("")
    gitops_page = [
        "# GitOps context",
        "",
        "> Generated view. Runtime authority still requires Flux status and runtime read-back.",
        "",
        "| Domain | Repository | Controller namespace |",
        "| --- | --- | --- |",
    ]
    for item in sorted(gitops, key=lambda value: value["domain"]):
        gitops_page.append(f"| `{item['domain']}` | {item['repository']} | `{item['controllerNamespace']}` |")
    gitops_page.append("")
    return {
        "Home.md": "\n".join(home),
        "Repository-Context.md": "\n".join(repository_page),
        "GitOps-Context.md": "\n".join(gitops_page),
    }


def write_wiki(check: bool) -> dict[str, Any]:
    directory = ROOT / "operations/developer-context/wiki"
    expected = wiki_pages()
    if check:
        stale = [name for name, content in expected.items() if not (directory / name).exists() or (directory / name).read_text() != content]
        if stale:
            raise ContextError(f"Wiki projection is stale: {stale}; run context.py wiki")
    else:
        directory.mkdir(parents=True, exist_ok=True)
        for name, content in expected.items():
            (directory / name).write_text(content)
    return {"directory": str(directory.relative_to(ROOT)), "pages": sorted(expected), "checked": check}


def git(*args: str) -> str:
    process = subprocess.run(["git", "-C", str(ROOT), *args], text=True, capture_output=True, check=False)
    if process.returncode:
        raise ContextError(process.stderr.strip() or f"git {' '.join(args)} failed")
    return process.stdout.strip()


def authority_remote() -> tuple[str, str]:
    remotes = set(git("remote").splitlines())
    remote = "github" if "github" in remotes else "origin"
    return remote, git("remote", "get-url", remote)


def create_worktree(branch: str, target: Path, module: str, objective: str) -> dict[str, Any]:
    if not re.fullmatch(r"codex/[a-z0-9][a-z0-9-]*", branch):
        raise ContextError("branch must match codex/<lowercase-task-name>")
    if target.exists():
        raise ContextError(f"target already exists: {target}")
    if git("status", "--porcelain"):
        raise ContextError("base checkout must be clean before creating a task worktree")
    remote, remote_url = authority_remote()
    authority = load(DECLARATION)["spec"]["authority"]
    if repository_key(remote_url) != repository_key(authority["repository"]):
        raise ContextError(f"{remote} does not resolve to the declared service-backend authority")
    subprocess.run(["git", "-C", str(ROOT), "fetch", remote, authority["branch"]], check=True)
    subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "-b", branch, str(target), f"{remote}/{authority['branch']}"], check=True)
    output = target / ".re8ch/task-context.json"
    task_context(output, module, objective, base_commit=git("rev-parse", f"{remote}/{authority['branch']}"))
    return {"path": str(target), "branch": branch, "base": f"{remote}/{authority['branch']}", "taskContext": str(output)}


def task_context(output: Path, module: str, objective: str, *, base_commit: str | None = None) -> dict[str, Any]:
    docs = {doc["metadata"]["name"]: (path, doc) for path, doc in module_documents()}
    if module not in docs:
        raise ContextError(f"unknown module: {module}")
    path, doc = docs[module]
    payload = {
        "apiVersion": "workspace.re8ch.com/v1alpha1",
        "kind": "TaskContext",
        "objective": objective,
        "module": module,
        "catalog": doc["metadata"]["catalog"],
        "repository": doc["spec"]["repository"]["url"],
        "base": {"repository": authority_remote()[1], "commit": base_commit or git("rev-parse", "HEAD")},
        "declaration": str(path.relative_to(ROOT)),
        "dependencies": doc["spec"].get("dependencies", []),
        "context": ["AGENTS.md", str(path.parent.relative_to(ROOT) / "MEMORY.md")],
        "ephemeral": True,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return {"path": str(output), "module": module}


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    index = commands.add_parser("index")
    index.add_argument("--check", action="store_true")
    wiki = commands.add_parser("wiki")
    wiki.add_argument("--check", action="store_true")
    task = commands.add_parser("task-context")
    task.add_argument("--module", required=True)
    task.add_argument("--objective", required=True)
    task.add_argument("--output", type=Path, default=ROOT / ".re8ch/task-context.json")
    worktree = commands.add_parser("worktree-create")
    worktree.add_argument("--branch", required=True)
    worktree.add_argument("--path", required=True, type=Path)
    worktree.add_argument("--module", required=True)
    worktree.add_argument("--objective", required=True)
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            result = validate()
        elif args.command == "index":
            validate()
            result = write_index(args.check)
        elif args.command == "wiki":
            validate()
            result = write_wiki(args.check)
        elif args.command == "task-context":
            validate()
            result = task_context(args.output, args.module, args.objective)
        else:
            validate()
            result = create_worktree(args.branch, args.path, args.module, args.objective)
    except ContextError as error:
        print(json.dumps({"healthy": False, "error": str(error)}, indent=2), file=sys.stderr)
        raise SystemExit(1) from error
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
