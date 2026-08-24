#!/usr/bin/env python3
"""RE8CH catalog, submodule lock and project-context controller."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class WorkspaceError(RuntimeError):
    pass


def operation(action: str, result: Any, *, target: str = "workspace") -> dict[str, Any]:
    op = f"op_{uuid.uuid4().hex}"
    return {"operationId": op, "action": action, "target": target, "result": result, "auditRef": f"audit://workspace/{op}"}


def git(path: Path, *argv: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-C", str(path), *argv], text=True, capture_output=True, check=False)
    if check and proc.returncode:
        raise WorkspaceError(proc.stderr.strip() or f"git {' '.join(argv)} failed")
    return proc.stdout.strip()


def repo_root(path: Path) -> Path | None:
    if not path.exists():
        return None
    value = git(path, "rev-parse", "--show-toplevel", check=False)
    return Path(value).resolve() if value else None


def repository_key(url: str) -> str:
    value = url.strip().removesuffix(".git")
    value = value.replace("git@github.com:", "github.com/").replace("git@gitlab.com:", "gitlab.com/")
    value = value.replace("git@github-re8ch-cloud-functions:", "github.com/")
    value = value.removeprefix("https://").removeprefix("ssh://git@")
    return value.lower()


PRUNED_DIRECTORIES = {
    ".git", ".cache", ".venv", "DerivedData", "Library", "node_modules",
    "Pods", "build", "dist", "target", "vendor",
}


def repository_candidates(start: Path) -> list[Path]:
    """Find Git worktrees without descending into common generated trees."""
    if not start.is_dir():
        return []
    found: list[Path] = []
    for current, directories, _files in os.walk(start):
        if ".git" in directories:
            found.append(Path(current))
            directories.remove(".git")
        directories[:] = [name for name in directories if name not in PRUNED_DIRECTORIES]
    return found


@dataclass(frozen=True)
class Module:
    name: str
    catalog: str
    ownership: str
    repository: str
    branch: str
    source_kind: str
    source_path: str
    metadata_policy: str
    write_policy: str
    dependencies: tuple[str, ...]
    source: Path


class Registry:
    def __init__(self, root: Path):
        self.root = root
        self.modules = self._load_modules()

    def _load_modules(self) -> dict[str, Module]:
        modules: dict[str, Module] = {}
        for path in sorted(self.root.glob("catalogs/*/modules/*/module.yaml")):
            doc = yaml.safe_load(path.read_text())
            meta, spec = doc["metadata"], doc["spec"]
            source = spec["source"]
            module = Module(
                meta["name"], meta["catalog"], spec["ownership"],
                spec["repository"]["url"], spec["repository"]["defaultBranch"],
                source["kind"], source["path"], source["metadataPolicy"],
                spec["writePolicy"], tuple(spec.get("dependencies", [])), path,
            )
            if module.name in modules:
                raise WorkspaceError(f"duplicate module: {module.name}")
            modules[module.name] = module
        return modules

    def validate(self) -> None:
        root_modules = []
        for module in self.modules.values():
            missing = set(module.dependencies) - self.modules.keys()
            if missing:
                raise WorkspaceError(f"{module.name}: missing dependencies {sorted(missing)}")
            if module.source_kind == "root":
                root_modules.append(module.name)
                if module.name != "service-backend" or module.source_path != ".":
                    raise WorkspaceError(f"{module.name}: only service-backend may use the root source")
                continue
            relative = module.source.parent.relative_to(self.root) / module.source_path
            entry = git(self.root, "ls-files", "--stage", str(relative), check=False)
            if not entry.startswith("160000 "):
                raise WorkspaceError(f"{module.name}: {relative} is not a git submodule entry")
            configured_path = git(
                self.root, "config", "-f", ".gitmodules", "--get",
                f"submodule.{module.catalog}/{module.name}.path", check=False,
            )
            configured_url = git(
                self.root, "config", "-f", ".gitmodules", "--get",
                f"submodule.{module.catalog}/{module.name}.url", check=False,
            )
            if configured_path != str(relative):
                raise WorkspaceError(f"{module.name}: .gitmodules path mismatch")
            if repository_key(configured_url) != repository_key(module.repository):
                raise WorkspaceError(f"{module.name}: .gitmodules URL mismatch")
        if root_modules != ["service-backend"]:
            raise WorkspaceError(f"expected only service-backend as root module, got {root_modules}")


class Controller:
    def __init__(self, root: Path):
        self.root = root
        self.registry = Registry(root)
        self.sources = yaml.safe_load((root / "workspace/sources.yaml").read_text())["spec"]

    def list(self) -> dict[str, Any]:
        return operation("workspace.list", [module.__dict__ | {"source": str(module.source.relative_to(self.root))} for module in self.registry.modules.values()])

    def scan(self) -> dict[str, Any]:
        found: dict[str, dict[str, Any]] = {}
        roots = [Path(item) for item in self.sources["localRoots"]]
        for start in roots:
            if not start.exists():
                continue
            candidates = repository_candidates(start)
            for candidate in candidates:
                root = repo_root(candidate)
                if root is None or str(root) in found:
                    continue
                found[str(root)] = self._observe(root)
        return operation("workspace.scan", {"repositories": sorted(found.values(), key=lambda item: item["path"]), "codexProjects": self._codex_projects()})

    def status(self) -> dict[str, Any]:
        rows = []
        lock = self._read_lock()
        for module in self.registry.modules.values():
            locked = lock.get("modules", {}).get(module.name)
            drift = []
            commit = self._module_commit(module)
            if locked and commit != locked["commit"]:
                drift.append("gitlink_lock_drift")
            rows.append({
                "module": module.name,
                "source": self._module_source(module),
                "commit": commit,
                "locked": locked,
                "drift": drift,
            })
        return operation("workspace.status", rows)

    def discovery(self) -> dict[str, Any]:
        scan = self.scan()["result"]
        published = {repository_key(module.repository) for module in self.registry.modules.values()}
        candidates = []
        for repo in scan["repositories"]:
            if not repo["repository"] or repository_key(repo["repository"]) in published:
                continue
            candidates.append({**repo, "status": "proposal-required", "autoPublished": False})
        return operation("workspace.discovery", {"candidates": candidates, "count": len(candidates)})

    def resolve_lock(self) -> dict[str, Any]:
        modules = {}
        for module in self.registry.modules.values():
            if module.source_kind == "root":
                continue
            modules[module.name] = {
                "repository": module.repository,
                "defaultBranch": module.branch,
                "commit": self._module_commit(module),
                "dependencies": list(module.dependencies),
            }
        schema_hash = hashlib.sha256((self.root / "schemas/workspace-module.schema.json").read_bytes()).hexdigest()
        return {"apiVersion": "workspace.re8ch.com/v1alpha1", "kind": "WorkspaceLock", "metadata": {"generation": int(time.time()), "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "schemaHash": schema_hash}, "modules": modules}

    def build_indexes(self) -> dict[str, Any]:
        projects, knowledge, tools, deployments = [], [], [], []
        for module in self.registry.modules.values():
            doc = yaml.safe_load(module.source.read_text())
            spec = doc["spec"]
            projects.append({
                "id": module.name,
                "catalog": module.catalog,
                "ownership": module.ownership,
                "repository": module.repository,
                "projectType": spec["projectType"],
                "source": self._module_source(module),
                "commit": self._module_commit(module),
            })
            knowledge.extend({"module": module.name, "ref": ref} for ref in spec.get("knowledge", []))
            memory_path = module.source.parent / "MEMORY.md"
            if memory_path.exists():
                knowledge.append({"module": module.name, "ref": str(memory_path.relative_to(self.root)), "kind": "agent-memory", "sha256": hashlib.sha256(memory_path.read_bytes()).hexdigest()})
            tools.extend({"module": module.name, "ref": ref} for ref in spec.get("tools", []))
            deployments.extend({"module": module.name, "ref": ref} for ref in spec.get("deployments", []))
        return {"projects": projects, "knowledge": knowledge, "tools": tools, "deployments": deployments}

    def context(self, module_name: str | None = None, query: str | None = None) -> dict[str, Any]:
        rows = []
        for module in self.registry.modules.values():
            if module_name and module.name != module_name:
                continue
            doc = yaml.safe_load(module.source.read_text())
            spec = doc["spec"]
            memory_path = module.source.parent / "MEMORY.md"
            row = {
                "module": module.name, "catalog": module.catalog,
                "ownership": module.ownership, "writePolicy": module.write_policy,
                "repository": module.repository, "defaultBranch": module.branch,
                "source": self._module_source(module),
                "commit": self._module_commit(module),
                "knowledge": spec.get("knowledge", []), "tools": spec.get("tools", []),
                "deployments": spec.get("deployments", []), "dependencies": list(module.dependencies),
                "memory": memory_path.read_text(errors="replace") if memory_path.exists() else "",
                "memoryAvailable": memory_path.exists(),
            }
            if query and query.lower() not in json.dumps(row, ensure_ascii=False).lower():
                continue
            rows.append(row)
        if module_name and not rows:
            raise WorkspaceError(f"unknown module: {module_name}")
        return operation("workspace.context", rows)

    def memory_audit(self) -> dict[str, Any]:
        rows = []
        for module in self.registry.modules.values():
            path = module.source.parent / "MEMORY.md"
            rows.append({"module": module.name, "catalog": module.catalog, "memory": str(path.relative_to(self.root)), "available": path.exists(), "bytes": path.stat().st_size if path.exists() else 0})
        return operation("workspace.memory-audit", {"modules": rows, "complete": all(row["available"] for row in rows)})

    def sync(self, *, write: bool) -> dict[str, Any]:
        desired = self.resolve_lock()
        current = self._read_lock()
        changed = current.get("modules", {}) != desired["modules"] or current.get("metadata", {}).get("schemaHash") != desired["metadata"]["schemaHash"]
        if write and changed:
            path = self.root / "workspace/workspace.lock.yaml"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(yaml.safe_dump(desired, sort_keys=True))
            temporary.replace(path)
        return operation("workspace.sync", {"changed": changed, "written": bool(write and changed), "lock": desired})

    def write_indexes(self) -> dict[str, Any]:
        indexes = self.build_indexes()
        output = self.root / "indexes"
        output.mkdir(parents=True, exist_ok=True)
        for name, content in indexes.items():
            path = output / f"{name}.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(content, indent=2, sort_keys=True) + "\n")
            temporary.replace(path)
        return operation("workspace.index", {"written": True, "counts": {name: len(value) for name, value in indexes.items()}})

    def _read_lock(self) -> dict[str, Any]:
        path = self.root / "workspace/workspace.lock.yaml"
        return yaml.safe_load(path.read_text()) if path.exists() else {"modules": {}}

    def _module_source(self, module: Module) -> str:
        if module.source_kind == "root":
            return "."
        return str(module.source.parent.relative_to(self.root) / module.source_path)

    def _module_commit(self, module: Module) -> str:
        if module.source_kind == "root":
            return "self"
        entry = git(self.root, "ls-files", "--stage", self._module_source(module), check=False)
        if not entry.startswith("160000 "):
            raise WorkspaceError(f"{module.name}: missing submodule gitlink")
        return entry.split()[1]

    @staticmethod
    def _observe(path: Path) -> dict[str, Any]:
        return {"path": str(path), "repository": git(path, "remote", "get-url", "origin", check=False), "branch": git(path, "branch", "--show-current", check=False), "commit": git(path, "rev-parse", "HEAD"), "dirty": bool(git(path, "status", "--porcelain", check=False))}

    def _codex_projects(self) -> list[dict[str, Any]]:
        db = Path(os.path.expanduser(self.sources["codexCatalog"]))
        if not db.exists():
            return []
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            rows = conn.execute("SELECT cwd,count(*) FROM local_thread_catalog WHERE missing_candidate=0 GROUP BY cwd ORDER BY count(*) DESC").fetchall()
        return [{"path": path, "threads": count, "git": bool(repo_root(Path(path)))} for path, count in rows if path]


def main() -> None:
    parser = argparse.ArgumentParser(prog="re8ch-workspace")
    script = Path(__file__).resolve()
    default_root = script.parents[2] if len(script.parents) > 2 else Path(os.getenv("WORKSPACE_ROOT", "/srv/service-backend"))
    parser.add_argument("--root", type=Path, default=default_root)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("list", "scan", "discovery", "status", "resolve-lock", "doctor", "memory-audit"):
        sub.add_parser(name)
    context = sub.add_parser("context")
    context.add_argument("--module")
    context.add_argument("--query")
    index = sub.add_parser("index")
    index.add_argument("--write", action="store_true")
    sync = sub.add_parser("sync")
    sync.add_argument("--write", action="store_true")
    args = parser.parse_args()
    controller = Controller(args.root)
    if args.command == "doctor":
        controller.registry.validate(); result = operation("workspace.doctor", {"healthy": True, "modules": len(controller.registry.modules)})
    elif args.command == "index": result = controller.write_indexes() if args.write else operation("workspace.index", controller.build_indexes())
    elif args.command == "sync": result = controller.sync(write=args.write)
    elif args.command == "resolve-lock": result = operation("workspace.resolve-lock", controller.resolve_lock())
    elif args.command == "context": result = controller.context(args.module, args.query)
    elif args.command == "memory-audit": result = controller.memory_audit()
    else: result = getattr(controller, args.command)()
    json.dump(result, sys.stdout, indent=2, default=str); print()


if __name__ == "__main__":
    main()
